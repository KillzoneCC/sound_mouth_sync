# sound_mouth_sync

ROS-пакет для управления OLED-дисплеем SSD1306 128x64 (I2C 0x3D) на голове робота Ainex (область рта). Два режима работы:

1. **emotion** — статичное выражение рта (happy, sad, neutral, ...) или пользовательские PNG-изображения
2. **oscillogram** — визуализация звука в реальном времени (осциллограмма всех звуков, воспроизводимых системой)

## Архитектура

```
┌───────────────────────┐       /mouth/audio_wave        ┌──────────────────────┐
│  audio_capture_node   │──────(Float32MultiArray)──────▶│   display_node       │
│  (захват звука)       │                                │   (OLED 0x3D)        │
│                       │──── /audio/level (Float32)     │                      │
└───────────────────────┘                                │  /mouth/mode ◀──(in) │
                                                         │  /mouth/emotion◀(in) │
                                                         │                      │
                                                         │  /mouth/current_mode │
                                                         │  /mouth/current_emot.│
                                                         └──────────────────────┘
```

Слой хранения команд эмоций/режима (`emotion_node`) включён в launch:

- внешние команды остаются прежними: `/mouth/mode`, `/mouth/emotion`
- `emotion_node` публикует `/mouth/effective_mode`, `/mouth/effective_emotion`
- `display_node` рендерит по `effective_*`, что снижает связность рендера и логики хранения команд.

Подробная архитектура: [doc/ARCHITECTURE.md](doc/ARCHITECTURE.md)

## Быстрый старт

```bash
# Запуск обеих нод
roslaunch sound_mouth_sync sound_mouth_sync.launch

# С нестандартной эмоцией при старте
roslaunch sound_mouth_sync sound_mouth_sync.launch default_emotion:=happy

# Отключить авто-переключение на осциллограмму
roslaunch sound_mouth_sync sound_mouth_sync.launch auto_mode:=false

# Через 2 минуты бездействия вместо 60 с — «спящая» эмоция
roslaunch sound_mouth_sync sound_mouth_sync.launch idle_sleep_sec:=120

# Только тишина как критерий сна (без ожидания /robot/is_moving) — осторожно, см. ниже
roslaunch sound_mouth_sync sound_mouth_sync.launch idle_require_movement_signal:=false
```

## Спящий режим и эмоция при падении

`display_node` подписывается на состояние тела от `joystick_control`:

- **Падение:** при `/robot/posture` = `fall_forward`, `fall_backward`, `fall_left`, `fall_right` на OLED показывается эмоция «злость» (`fall_emotion`, по умолчанию `angry`), осциллограмма отключается до возврата `stand`. Рисуется «злой» рот с зубами и царапиной; если положить `resources/emotions/angry.png` (или `.bmp`/`.gif`), будет показана эта картинка вместо векторной отрисовки.
- **Долгое бездействие:** если нет **речи** (нет слышимого уровня на `/mouth/audio_wave` и не активен режим осциллограммы) и нет **ходьбы** (`/robot/is_moving` = false) в течение `idle_sleep_sec` (по умолчанию 60 с), показывается `idle_sleep_emotion` (по умолчанию `sleepy`). Для `sleepy` без файла `resources/emotions/sleepy.*` используется анимация «сигарета + дым»; свой `sleepy.png` отключает анимацию и показывает статичную картинку.

**Настройка времени и поведения**

| Где | Параметр |
|-----|----------|
| `config/sound_mouth_sync.yaml` → `display` | `idle_sleep_sec`, `idle_sleep_enabled`, `idle_sleep_emotion`, `fall_emotion` |
| Launch | `idle_sleep_sec`, `idle_sleep_enabled`, `idle_require_movement_signal` |
| Нода | `~idle_sleep_sec`, `~posture_topic`, `~movement_topic`, … |

По умолчанию `idle_require_movement_signal:=true`: пока ни разу не пришло сообщение на `/robot/is_moving` (например, нет `joystick_control`), переход в спящий режим **не выполняется** — чтобы не включать «сон» ошибочно. Для отладки только по тишине задайте `idle_require_movement_signal:=false`.

**VR / teleop без джойстика:** если поход идёт через другой пакет и **не** публикуется `/robot/is_moving`, дисплей может считать робота стоящим. Тогда либо публикуйте `Bool` на тот же топик из своего узла, либо используйте `idle_require_movement_signal:=false` и учитывайте только тишину. Альтернатива: в YAML задать `movement_topic: /walking/is_walking` (топик из `ainex_controller`), если он есть в вашем bringup.

## Топики

### Входные (подписка)

| Топик | Тип | Описание |
|-------|-----|----------|
| `/mouth/mode` | `std_msgs/String` | Режим: `"emotion"` или `"oscillogram"` |
| `/mouth/emotion` | `std_msgs/String` | Эмоция: `neutral`, `happy`, `sad`, `angry`, `surprised`, `excited`, `sleepy`, `love`, `confused`, `scared`, `bored`, `calm`, `disgusted`, `tired` |
| `/mouth/audio_wave` | `std_msgs/Float32MultiArray` | 128 значений от -1.0 до 1.0 для осциллограммы |
| `/robot/posture` | `std_msgs/String` | `stand` или `fall_*` — публикует `joystick_control` (`ainex_peripherals`) |
| `/robot/is_moving` | `std_msgs/Bool` | `true`, пока робот идёт по джойстику — тот же узел |

### Выходные (публикация)

| Топик | Тип | Описание |
|-------|-----|----------|
| `/mouth/current_mode` | `std_msgs/String` (latch) | Текущий режим дисплея |
| `/mouth/current_emotion` | `std_msgs/String` (latch) | Текущая эмоция |
| `/audio/level` | `std_msgs/Float32` | RMS-уровень звука 0..1 (совместимость) |

## Примеры rostopic pub для отладки

### Переключение режима

```bash
# Режим эмоций
rostopic pub -1 /mouth/mode std_msgs/String "data: 'emotion'"

# Режим осциллограммы
rostopic pub -1 /mouth/mode std_msgs/String "data: 'oscillogram'"
```

### Установка эмоции

```bash
# Улыбка
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'happy'"

# Грусть
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'sad'"

# Удивление
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'surprised'"

# Нейтральное выражение
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'neutral'"
```

### Публикация тестовой осциллограммы

```bash
# Синусоида (2 периода по 128 точкам)
rostopic pub -1 /mouth/audio_wave std_msgs/Float32MultiArray \
  "layout:
  dim: []
  data_offset: 0
data: [$(python3 -c "import math; print(','.join(str(round(math.sin(2*math.pi*2*i/128),3)) for i in range(128)))")]"

# Прямая линия (тишина)
rostopic pub -1 /mouth/audio_wave std_msgs/Float32MultiArray \
  "layout:
  dim: []
  data_offset: 0
data: [$(python3 -c "print(','.join(['0.0']*128))")]"
```

### Режим сна → осциллограмма → снова рот

При `auto_mode: true` дисплей слушает и **`/mouth/audio_wave`**, и **`/audio/level`** (пороги в `scripts/mouth_audio_gates.py`). Если в сне осциллограмма не появляется, проверьте, что звук идёт в тот же PulseAudio, что и `audio_capture_node`, и что `rostopic hz /audio/level` не нулевой во время воспроизведения.

```bash
# Имитация сильного звука без файла (должен включить осциллограмму даже из idle-sleep)
rostopic pub -r 20 /mouth/audio_wave std_msgs/Float32MultiArray \
  "layout:
  dim: []
  data_offset: 0
data: [$(python3 -c "import math; print(','.join(str(round(0.5*math.sin(2*math.pi*i/16),3)) for i in range(128)))")]"

# Или только уровень (как при тихом файле)
rostopic pub -r 20 /audio/level std_msgs/Float32 "data: 0.05"
```

После остановки публикации через `silence_return_sec` снова покажется эмоция (и при длительной тишине — сон по `idle_sleep_sec`).

### Проверка текущего состояния

```bash
# Текущий режим
rostopic echo /mouth/current_mode

# Текущая эмоция
rostopic echo /mouth/current_emotion

# Уровень звука
rostopic echo /audio/level
```

## Воспроизведение звука

`audio_capture_node` запускает PulseAudio-сервер внутри Docker-контейнера, который эксклюзивно владеет USB-звуковой картой. Все звуки, проходящие через этот сервер, автоматически отображаются на OLED-дисплее как осциллограмма.

### Из Docker-контейнера (рекомендуется)

```bash
# Простейший тест
paplay /path/to/sound.wav

# Или через aplay
aplay /path/to/sound.wav
```

Любые ROS-ноды внутри контейнера (TTS, sound_play и т.д.) автоматически используют этот PulseAudio.

### С хоста (Raspberry Pi)

```bash
# Однократная настройка:
source $(rospack find sound_mouth_sync)/scripts/setup_host_audio.sh

# Проверить подключение:
source $(rospack find sound_mouth_sync)/scripts/setup_host_audio.sh --check

# Теперь любой звук пойдёт через Docker:
paplay /usr/share/sounds/alsa/Front_Left.wav
```

Подробное руководство для разработчиков (TTS, Python, ROS sound_play): [doc/AUDIO_PLAYBACK.md](doc/AUDIO_PLAYBACK.md)

### Регулировка громкости

```bash
# Установить громкость (0–100%):
pactl set-sink-volume usb_output 80%

# Увеличить / уменьшить:
pactl set-sink-volume usb_output +10%
pactl set-sink-volume usb_output -10%

# Текущая громкость:
pactl get-sink-volume usb_output
```

Подробнее (Python, ROS, хост): [doc/AUDIO_PLAYBACK.md](doc/AUDIO_PLAYBACK.md#регулировка-громкости)

## Создание пользовательских эмоций

Поместите изображение в `resources/emotions/` с именем, соответствующим эмоции:

```
resources/emotions/
  wink.png        → rostopic pub -1 /mouth/emotion std_msgs/String "data: 'wink'"
  yawn.png        → rostopic pub -1 /mouth/emotion std_msgs/String "data: 'yawn'"
```

**Требования к изображению:**

- Формат: PNG, BMP или GIF (без анимации)
- Размер: 128 x 64 пикселей
- Цвет: черно-белое (1-bit). Белые пиксели = светятся на OLED
- Рекомендуется: черный фон, белый рисунок рта

**Как создать:**

```bash
# Конвертация из любого PNG:
convert input.png -resize 128x64! -monochrome resources/emotions/myemotion.png

# Или через Python / Pillow:
python3 -c "
from PIL import Image
img = Image.open('input.png').resize((128, 64)).convert('1')
img.save('resources/emotions/myemotion.png')
"
```

После добавления файла перезапустите ноду — изображение загрузится автоматически.

## Зависимости

**Python (pip3):**
- `luma.oled` — драйвер OLED SSD1306
- `Pillow` — обработка изображений
- `numpy` — обработка аудиосигнала
- `PyYAML` — чтение конфига (обычно уже установлен)

**Системные (внутри Docker-контейнера):**
- `pulseaudio` — аудио-сервер
- `pulseaudio-utils` — `pactl`, `paplay`, `parec`
- `alsa-utils` — `aplay`, `arecord`
- I2C включён (`sudo raspi-config` → Interfaces → I2C)

**На хосте (для воспроизведения звука с Raspberry Pi):**
- `pulseaudio-utils` — `pactl`, `paplay`

```bash
pip3 install luma.oled Pillow numpy
sudo apt install pulseaudio pulseaudio-utils alsa-utils
```

## Параметры (rosparam / launch args)

| Параметр | По умолчанию | Описание |
|----------|-------------|----------|
| `default_emotion` | `neutral` | Эмоция при запуске |
| `auto_mode` | `true` | Авто-переключение на осциллограмму при звуке |
| `silence_return_sec` | `3.0` | Секунд тишины до возврата к эмоции |
| `idle_sleep_enabled` | `true` | Включить «спящую» эмоцию после бездействия |
| `idle_sleep_sec` | `60.0` | Секунд без речи и без ходьбы до `idle_sleep_emotion` |
| `idle_require_movement_signal` | `true` | Не уходить в sleep, пока не было `/robot/is_moving` |
| `idle_sleep_emotion` | `sleepy` | Эмоция простоя (YAML `display`) |
| `fall_emotion` | `angry` | Эмоция при падении (YAML `display`) |
| `rate` | `48000` | Частота дискретизации (Гц), должна совпадать с USB-картой |
| `chunk_size` | `1024` | Сэмплов на один фрагмент |

## Устранение неполадок

### USB-звуковая карта не работает после перезагрузки

После перезагрузки Raspberry Pi USB-звуковая карта может не инициализироваться.
Проверить: `aplay -l` — если USB Audio Device отсутствует в списке:

```bash
# Сброс USB-устройства (без физического переподключения)
sudo $(rospack find sound_mouth_sync)/scripts/usb_audio_reset.sh

# Проверить, что карта появилась
aplay -l | grep USB
```

### Осциллограмма не отображается при воспроизведении звука

Запустите диагностику:

```bash
$(rospack find sound_mouth_sync)/scripts/audio_diag.sh
# Или с тестом захвата:
$(rospack find sound_mouth_sync)/scripts/audio_diag.sh --test
```

Типичные причины:
- PulseAudio не запущен внутри контейнера (нода запускает автоматически)
- USB-карта не является default sink в PulseAudio
- Звук воспроизводится на хосте без `PULSE_SERVER` — не попадает в Docker

Ручная проверка:

```bash
# PulseAudio sinks (должен быть usb_output)
pactl list sinks short

# Monitor-источники (должен быть usb_output.monitor)
pactl list sources short

# TCP-модуль загружен?
pactl list modules short | grep tcp

# Тест: проиграть звук и послушать уровень
rostopic echo /audio/level
```

### Нет звука с хоста (VLC, aplay и т.д.)

Хост должен направлять звук в PulseAudio контейнера:

```bash
source $(rospack find sound_mouth_sync)/scripts/setup_host_audio.sh --check
```

См. подробности в [doc/AUDIO_PLAYBACK.md](doc/AUDIO_PLAYBACK.md#воспроизведение-с-хоста-raspberry-pi).

### OLED-дисплей показывает перевёрнутое изображение

Параметр `rotate` в `config/sound_mouth_sync.yaml` секция `hardware`:
- `0` — нормальная ориентация
- `2` — поворот на 180° (по умолчанию, для перевёрнутого монтажа)
