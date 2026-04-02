# Воспроизведение звука — руководство разработчика

## Архитектура аудио

`audio_capture_node` при старте поднимает **PulseAudio-сервер** внутри Docker-контейнера. Этот сервер — единственный «владелец» USB-звуковой карты. Все звуки, которые должны воспроизводиться физически и отображаться на осциллограмме, **должны идти через этот PulseAudio**.

```
┌────────────────────────────────────────────────────────┐
│  Docker Container                                      │
│                                                        │
│  PulseAudio Server                                     │
│  ├── module-alsa-sink (usb_output → plughw:N,0)       │
│  ├── module-native-protocol-unix (локальный сокет)     │
│  └── module-native-protocol-tcp  (порт 4713)          │
│                                                        │
│  audio_capture_node ← parec ← usb_output.monitor      │
│  display_node ← /mouth/audio_wave                      │
│                                                        │
│  TTS / paplay / любой звук внутри контейнера           │
│    └── автоматически → PulseAudio → usb_output         │
└────────────────────────────────────────────────────────┘
        ↑ TCP :4713
┌───────┴────────────────────────────────────────────────┐
│  Raspberry Pi Host                                     │
│  PULSE_SERVER=tcp:127.0.0.1:4713                       │
│  VLC / aplay / paplay / любое приложение               │
└────────────────────────────────────────────────────────┘
```

## Воспроизведение из Docker (рекомендуемый способ)

Внутри контейнера PulseAudio доступен по Unix-сокету — всё работает автоматически.

### paplay (простейший способ)

```bash
paplay /path/to/audio.wav
```

### Python — subprocess + paplay

```python
import subprocess

def play_sound(path):
    """Воспроизвести WAV/OGG через PulseAudio."""
    subprocess.Popen(["paplay", path])
```

### Python — subprocess + aplay через PulseAudio

```bash
# aplay тоже работает, т.к. ALSA-sink принадлежит PulseAudio
aplay /path/to/audio.wav
```

### ROS sound_play

Если используется пакет `sound_play`, убедитесь что он работает внутри того же контейнера:

```python
from sound_play.libsoundplay import SoundClient

sc = SoundClient()
rospy.sleep(1)  # дождаться подключения
sc.playWave('/path/to/sound.wav')
```

### Python TTS (pyttsx3)

```python
import pyttsx3

engine = pyttsx3.init()
engine.say("Привет, я робот!")
engine.runAndWait()
```

`pyttsx3` использует `espeak` → PulseAudio → USB-карта → осциллограмма.

### Google TTS (gTTS)

```python
from gtts import gTTS
import subprocess, tempfile, os

tts = gTTS("Привет мир", lang="ru")
with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
    path = f.name
    tts.save(path)

subprocess.call(["paplay", path])
os.unlink(path)
```

> **Примечание:** `paplay` может не воспроизводить MP3 напрямую. Конвертируйте в WAV:
> ```bash
> ffmpeg -i file.mp3 -f wav -acodec pcm_s16le file.wav
> ```

### ROS node — минимальный пример TTS

```python
#!/usr/bin/env python3
import rospy, subprocess, tempfile, os

def speak(text, lang="ru"):
    from gtts import gTTS
    tts = gTTS(text, lang=lang)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    tts.save(path.replace(".wav", ".mp3"))
    subprocess.call(["ffmpeg", "-y", "-i", path.replace(".wav", ".mp3"),
                     "-f", "wav", "-acodec", "pcm_s16le", path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.call(["paplay", path])
    os.unlink(path)
    os.unlink(path.replace(".wav", ".mp3"))

rospy.init_node("tts_example")
speak("Привет! Я робот Ainex.")
```

## Воспроизведение с хоста (Raspberry Pi)

Приложения на хосте (VLC, `aplay`, `speaker-test` и т.д.) по умолчанию не имеют доступа к PulseAudio внутри Docker. Для этого `audio_capture_node` открывает TCP-порт **4713**.

### Быстрая настройка

```bash
# Из корня пакета:
source $(rospack find sound_mouth_sync)/scripts/setup_host_audio.sh

# Проверить подключение:
source $(rospack find sound_mouth_sync)/scripts/setup_host_audio.sh --check

# Сохранить в ~/.bashrc (постоянно):
source $(rospack find sound_mouth_sync)/scripts/setup_host_audio.sh --permanent
```

### Ручная настройка

```bash
export PULSE_SERVER=tcp:127.0.0.1:4713

# Проверка:
pactl info

# Тест звука:
speaker-test -t sine -f 440 -l 1 -p 2
paplay /usr/share/sounds/alsa/Front_Left.wav
```

### Требования на хосте

```bash
sudo apt install pulseaudio-utils
```

> PulseAudio-сервер на хосте запускать **не нужно** — `PULSE_SERVER` перенаправит клиенты в Docker.

### Docker: проброс порта

Если Docker-контейнер использует `--network=host`, порт 4713 уже доступен. Если используется bridge-сеть, добавьте в `docker run` или `docker-compose.yml`:

```yaml
ports:
  - "4713:4713"
```

## Регулировка громкости

### Из командной строки (pactl)

```bash
# Текущая громкость:
pactl get-sink-volume usb_output

# Установить громкость (0–100%):
pactl set-sink-volume usb_output 80%

# Увеличить на 10%:
pactl set-sink-volume usb_output +10%

# Уменьшить на 10%:
pactl set-sink-volume usb_output -10%

# Отключить/включить звук (mute/unmute):
pactl set-sink-mute usb_output toggle
```

### Из Python

```python
import subprocess

def set_volume(percent):
    """Установить громкость USB-карты (0–100)."""
    subprocess.call(["pactl", "set-sink-volume", "usb_output", "%d%%" % percent])

def change_volume(delta):
    """Изменить громкость на delta процентов (+/-)."""
    sign = "+" if delta >= 0 else ""
    subprocess.call(["pactl", "set-sink-volume", "usb_output", "%s%d%%" % (sign, delta)])
```

### Из ROS-ноды

```python
import subprocess, rospy
from std_msgs.msg import Int32

def volume_callback(msg):
    subprocess.call(["pactl", "set-sink-volume", "usb_output", "%d%%" % msg.data])

rospy.Subscriber("/audio/volume", Int32, volume_callback, queue_size=1)
```

### Громкость с хоста

При подключении через `PULSE_SERVER=tcp:127.0.0.1:4713`:

```bash
PULSE_SERVER=tcp:127.0.0.1:4713 pactl set-sink-volume usb_output 70%
```

### amixer (ALSA, напрямую)

```bash
# Список регуляторов:
amixer -c 2 scontrols

# Установить громкость (если есть регулятор Speaker или PCM):
amixer -c 2 set Speaker 80%
```

> **Примечание**: не все USB-карты поддерживают аппаратную регулировку громкости через ALSA. В этом случае используйте `pactl` (программная регулировка PulseAudio).

## Как работает осциллограмма

1. Любое приложение воспроизводит звук → PulseAudio → `usb_output` sink
2. `audio_capture_node` читает `usb_output.monitor` через `parec`
3. Сигнал преобразуется в 128-точечную осциллограмму → `/mouth/audio_wave`
4. `display_node` отрисовывает волну на OLED
5. При тишине > `silence_return_sec` секунд → возврат к режиму эмоций

**Поэтому для отображения осциллограммы достаточно, чтобы звук проходил через PulseAudio-сервер контейнера.**

### Связь с OLED рта (I2C)

Осциллограмма рисуется на дисплее по адресу **`hardware.i2c_address`** (штатно **0x3D**) в `mouth_display_node`. **Аудио-цепочка** (PulseAudio → `/mouth/audio_wave`) **не определяет**, какой физический модуль слушает **0x3C** vs **0x3D** — это задаёт **железо** (перемычка ADDR). Если на «рту» видна **та же** картинка, что на системном OLED (SSID, IP…), сначала проверьте **`i2cdetect -y 1`** и адреса двух модулей, а не только звук. Подробно: [ARCHITECTURE.md](ARCHITECTURE.md), [SECOND_DISPLAY_ARCHITECTURE.md](SECOND_DISPLAY_ARCHITECTURE.md) §8.

### ALSA → PulseAudio (автоматически)

`audio_capture_node` при старте записывает `/etc/asound.conf`, который перенаправляет ALSA default device через PulseAudio. Это означает, что **`aplay`, `arecord` и любые ALSA-приложения** автоматически проходят через PulseAudio и видны на осциллограмме:

```bash
aplay file.wav        # ✅ звук идёт через PA → осциллограмма работает
paplay file.wav       # ✅ напрямую через PA
speaker-test -t sine  # ✅ через PA
```

Если `/etc/asound.conf` отсутствует или повреждён, `aplay` может обойти PulseAudio и пойти напрямую в ALSA hw — тогда осциллограмма не увидит звук. Пересоздать:
```bash
cat > /etc/asound.conf << 'EOF'
pcm.!default { type pulse }
ctl.!default { type pulse }
EOF
```

## Troubleshooting

### Осциллограмма в топиках есть, но на экране рта «не та» картинка (SSID/IP)

Если **`rostopic echo /audio/level`** реагирует на звук и **`/mouth/current_mode`** переходит в `oscillogram`, а физически на модуле рта отображается **системная** статистика:

1. Это **не** типичная ошибка PulseAudio — трафик статуса идёт на I2C **0x3C** из `oled_display.py`; «чужая» картинка на втором экране почти всегда означает **дублирующий адрес 0x3C** на двух SSD1306 или отсутствие **0x3D** на шине.
2. Выполните **`sudo i2cdetect -y 1`** — ожидаются **оба**: `3c` и `3d`.
3. См. чеклист: [README.md](../README.md) (два OLED), [SECOND_DISPLAY_ARCHITECTURE.md](SECOND_DISPLAY_ARCHITECTURE.md) §8, лог **`mouth_display_node`** (строки про опрос шины и **DIAGNOSTIC**).

### Нет звука / осциллограмма не реагирует

```bash
# 1. Проверить что audio_capture_node запущен:
rosnode list | grep audio

# 2. Проверить PulseAudio (изнутри контейнера):
pactl info
pactl list sinks short    # должен быть usb_output
pactl list sources short  # должен быть usb_output.monitor

# 3. Проверить TCP-модуль:
pactl list modules short | grep tcp

# 4. Проверить уровень (при воспроизведении звука):
rostopic echo /audio/level   # должен быть > 0

# 5. Диагностика:
$(rospack find sound_mouth_sync)/scripts/audio_diag.sh --test
```

### Хост не подключается к PulseAudio

```bash
# Проверить что порт открыт (внутри контейнера):
ss -tlnp | grep 4713

# Проверить из хоста:
PULSE_SERVER=tcp:127.0.0.1:4713 pactl info

# Если сеть не host — проверить проброс порта:
docker port <container_id>
```

### USB-карта не найдена

```bash
# Проверить ALSA-устройства:
aplay -l | grep USB

# Если нет — сбросить USB:
sudo $(rospack find sound_mouth_sync)/scripts/usb_audio_reset.sh
```

### PulseAudio не запускается

```bash
# Убить старый экземпляр:
pulseaudio --kill
sleep 1

# Запустить вручную:
pulseaudio --start --exit-idle-time=-1

# Проверить:
pactl info
```
