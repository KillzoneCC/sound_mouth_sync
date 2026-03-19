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

Подробная архитектура: [doc/ARCHITECTURE.md](doc/ARCHITECTURE.md)

## Быстрый старт

```bash
# Запуск обеих нод
roslaunch sound_mouth_sync sound_mouth_sync.launch

# С нестандартной эмоцией при старте
roslaunch sound_mouth_sync sound_mouth_sync.launch default_emotion:=happy

# Захват звука через PulseAudio вместо PipeWire
roslaunch sound_mouth_sync sound_mouth_sync.launch source:=pulse_monitor

# Отключить авто-переключение на осциллограмму
roslaunch sound_mouth_sync sound_mouth_sync.launch auto_mode:=false
```

## Топики

### Входные (подписка)

| Топик | Тип | Описание |
|-------|-----|----------|
| `/mouth/mode` | `std_msgs/String` | Режим: `"emotion"` или `"oscillogram"` |
| `/mouth/emotion` | `std_msgs/String` | Эмоция: `neutral`, `happy`, `sad`, `angry`, `surprised`, `excited`, `sleepy`, `love`, `confused`, `scared`, `bored`, `calm`, `disgusted`, `tired` |
| `/mouth/audio_wave` | `std_msgs/Float32MultiArray` | 128 значений от -1.0 до 1.0 для осциллограммы |

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

### Проверка текущего состояния

```bash
# Текущий режим
rostopic echo /mouth/current_mode

# Текущая эмоция
rostopic echo /mouth/current_emotion

# Уровень звука
rostopic echo /audio/level
```

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
- `PyYAML` — чтение конфига (обычно уже установлен)

**Системные:**
- `pipewire` + `pipewire-pulse` — захват звука с выхода (по умолчанию)
- или `pulseaudio-utils` — для режима `pulse_monitor`
- или `alsa-utils` — для режима `alsa`
- I2C включён (`sudo raspi-config` → Interfaces → I2C)

```bash
pip3 install luma.oled Pillow
sudo apt install pipewire pipewire-pulse alsa-utils
```

## Параметры (rosparam / launch args)

| Параметр | По умолчанию | Описание |
|----------|-------------|----------|
| `default_emotion` | `neutral` | Эмоция при запуске |
| `auto_mode` | `true` | Авто-переключение на осциллограмму при звуке |
| `silence_return_sec` | `3.0` | Секунд тишины до возврата к эмоции |
| `source` | `pipewire_monitor` | Источник захвата: `pipewire_monitor`, `pulse_monitor`, `alsa` |
| `pulse_source` | `""` (авто) | Имя Pulse-источника |
| `device` | `""` (авто) | ALSA-устройство (авто-определение USB-карты если пусто) |
| `rate` | `16000` | Частота дискретизации (Гц) |
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
- PipeWire daemon не запущен (нода попытается запустить автоматически)
- Нет session manager (pipewire-media-session / wireplumber)
- USB-карта не является default sink в PulseAudio
- Переменная `PIPEWIRE_CONFIG_FILE` указывает на несовместимый конфиг

Ручная проверка:

```bash
# Запущены ли все компоненты PipeWire?
pgrep -a pipewire

# PulseAudio sinks (должен быть USB Audio)
pactl list sinks short

# Monitor-источники (нужен .monitor для захвата)
pactl list sources short

# Тест: проиграть звук и послушать уровень
rostopic echo /audio/level
```

### OLED-дисплей показывает перевёрнутое изображение

Параметр `rotate` в `config/sound_mouth_sync.yaml` секция `hardware`:
- `0` — нормальная ориентация
- `2` — поворот на 180° (по умолчанию, для перевёрнутого монтажа)
