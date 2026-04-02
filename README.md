# sound_mouth_sync

ROS-пакет для управления OLED-дисплеем SSD1306 128x64 (I2C 0x3D) на голове робота Ainex (область рта). Два режима работы:

1. **emotion** — статичное выражение рта (happy, sad, neutral, **cute**, …) или пользовательские PNG-изображения; картинка на OLED **0x3D** обновляется по топикам ниже
2. **oscillogram** — визуализация звука в реальном времени (осциллограмма всех звуков, воспроизводимых системой)

## Архитектура

```
  внешние узлы / rostopic
         │  /mouth/mode          /mouth/effective_mode  ────────┐
         │  /mouth/emotion       /mouth/effective_emotion ────┤
         ▼                              ▲                      │
┌────────────────────┐                  │                      │
│   emotion_node     │  latch           │                      │
│ (mouth_emotion_    │──────────────────┘                      │
│      node)         │                                         ▼
└────────────────────┘                              ┌──────────────────────┐
         ▲                                          │   display_node       │
         │  опция: круг эмоций                       │   (OLED 0x3D)        │
         │  см. блок ниже                           │                      │
                                                     │  sub: effective_*    │
┌───────────────────────┐  /mouth/audio_wave         │  sub: audio_wave,     │
│  audio_capture_node   │  (Float32MultiArray) ─────▶│       audio/level    │
│  (захват звука)       │                            │  pub: current_mode   │
│                       │  /audio/level (Float32) ──▶│       current_emotion│
└───────────────────────┘                            └──────────────────────┘
```

**Поток данных (согласуется со схемой):** внешний API не меняется — **`/mouth/mode`**, **`/mouth/emotion`**. Их принимает **`emotion_node`** и публикует с защёлкой **`/mouth/effective_mode`**, **`/mouth/effective_emotion`**. **`display_node`** подписан **только** на **`effective_*`** (не на «сырые» топики) и выводит картинку на **I2C 0x3D**, публикуя фактическое состояние в **`/mouth/current_*`**.

| № в launch | ROS-имя ноды | Скрипт | Роль на схеме |
|------------|----------------|--------|----------------|
| 1 | `mouth_emotion_node` | `scripts/emotion_node.py` | Блок слева: внешние команды → latch `effective_*` |
| 2 | `mouth_display_node` | `scripts/display_node.py` | Блок справа: `effective_*`, аудио, постура → OLED **0x3D**, `current_*` |
| 3 | `mouth_audio_capture_node` | `scripts/audio_capture_node.py` | Нижний блок: PulseAudio + `parec` → волна и уровень |

Конфиг: `config/sound_mouth_sync.yaml`; пиксели эмоций: `scripts/mouth_emotion_render.py` (импорт из `display_node`). Опциональный **демо-круг** эмоций в `emotion_node` — один раз описан в [примерах ниже](#демо-круг-эмоций-emotion_node).

Подробная схема: [doc/ARCHITECTURE.md](doc/ARCHITECTURE.md). Блок-схема Visio (**IIC ADDRESS SELECT**): [doc/VISIO_SCHEME_OLED_JUMPER.md](doc/VISIO_SCHEME_OLED_JUMPER.md).

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
- **Долгое бездействие:** если нет **речи** (нет слышимого уровня на `/mouth/audio_wave` и не активен режим осциллограммы) и нет **ходьбы** (`/robot/is_moving` = false) в течение `idle_sleep_sec` (по умолчанию 60 с), показывается `idle_sleep_emotion` (по умолчанию `sleepy`). Если в `resources/idle_faces/` есть GIF или папки PNG — **случайно** выбирается одна анимация до пробуждения; если папка пуста и нет `sleepy.*` в `resources/emotions/` — встроенная «сигарета + дым»; свой `sleepy.png` даёт статичную картинку.

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
| `/mouth/emotion` | `std_msgs/String` | Эмоция: `neutral`, `happy`, `sad`, `angry`, `surprised`, `excited`, `sleepy`, `love`, `cute` (милота), `confused`, `scared`, `bored`, `calm`, `disgusted`, `tired`, `cat` |
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

Команды идут на **`/mouth/emotion`** → `emotion_node` → **`/mouth/effective_emotion`** → `display_node` → физический **рот SSD1306 по I2C 0x3D**. Если сейчас активна осциллограмма, сначала переключите режим командой из подраздела **«Переключение режима»** выше.

Имена ниже совпадают с **`EMOTION_CYCLE_SEQUENCE`** в `scripts/mouth_emotion_render.py` (и с демо-кругом). Для ручного вызова **порядок не важен** — публикуйте любую строку, когда режим уже `emotion`.

```bash
# --- тот же порядок, что в круге эмоций (удобно копировать по списку) ---
# Подписи — ориентир по встроенной векторной отрисовке (mouth_emotion_render.draw_emotion), иначе PNG из resources/emotions/

# neutral — нейтрально: прямая линия рта
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'neutral'"
# happy — радость: дуга вверх (улыбка)
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'happy'"
# sad — грусть: дуга вниз (хмурый рот)
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'sad'"
# angry — злость: в векторе та же прямая линия, что neutral; выразительность даёт angry.png
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'angry'"
# surprised — удивление: круглый овал «О»
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'surprised'"
# excited — восторг: дуга вверх (в коде совпадает с happy; отличить можно PNG excited.*)
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'excited'"
# love — нежность: улыбка чуть приподнята вверх
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'love'"

# cute — милота: обычно cute.png; без файла — «uwu» глаза и маленький ротик
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'cute'"

# confused — недоумение: волнистая / зигзаг-линия
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'confused'"
# scared — страх: круглый рот (овал, похож на surprised)
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'scared'"
# bored — скука: широкая ровная линия рта (шире, чем у tired)
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'bored'"
# calm — спокойствие: лёгкая короткая улыбка (дуга поменьше)
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'calm'"
# disgusted — отвращение: дуга вниз (перекошенная гримаса)
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'disgusted'"
# tired — усталость: короткий рот чуть ниже + намёк на «тяжёлые» уголки глаз (не путать с bored)
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'tired'"

# sleepy — сонный вид: без sleepy.* — сигарета и дым; с PNG — ваша картинка
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'sleepy'"

# sleep — сон: покадровая анимация sleep_frame* в resources/emotions/ (если есть)
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'sleep'"
# cat — кошка: покадровая анимация cat_frame* в resources/emotions/ (если есть)
rostopic pub -1 /mouth/emotion std_msgs/String "data: 'cat'"
```

Проверка факта на шине рта:

```bash
rostopic echo /mouth/effective_emotion
rostopic echo /mouth/current_emotion
```

<a id="demo-emotion-cycle"></a>

### Демо-круг эмоций (`emotion_node`)

Опционально внутри **`emotion_node`** включается **таймер**: раз в **`~emotion_cycle_interval_sec`** следующее имя из **`EMOTION_CYCLE_SEQUENCE`** в `scripts/mouth_emotion_render.py` публикуется на **`/mouth/effective_emotion`**, **`display_node`** обновляет кадр на **0x3D**. Порядок фиксирован:

`neutral` → `happy` → `sad` → `angry` → `surprised` → `excited` → `love` → **`cute`** → `confused` → `scared` → `bored` → `calm` → `disgusted` → `tired` → `sleepy` → `sleep` → `cat` → (снова с начала).

| Параметр (на `mouth_emotion_node`) | По умолчанию | Смысл |
|-----------------------------------|---------------|--------|
| `~emotion_cycle_enabled` | `false` | Включить автоматический круг |
| `~emotion_cycle_interval_sec` | `3.0` | Пауза между шагами (сек), минимум ~0.3 |

**Поведение:** при **`emotion_cycle_enabled:=true`** при старте эффективный режим принудительно **`emotion`** (не нужно отдельно слать `/mouth/mode` для витрины). Пока **`/mouth/effective_mode` = `oscillogram`**, счётчик круга **не растёт**. Таймер **перебивает** ручные `rostopic pub /mouth/emotion` — для ручной отладки держите круг выключенным. В типичном демо рядом поднимают **`display_node`** и **`audio_capture_node`** или целиком `roslaunch sound_mouth_sync sound_mouth_sync.launch` с теми же параметрами в XML.

**Включение** (один вариант):

1. В узле `mouth_emotion_node` в launch:
   ```xml
   <param name="emotion_cycle_enabled" value="true"/>
   <param name="emotion_cycle_interval_sec" value="3.0"/>
   ```
2. Или **только** `emotion_node` (если второй экземпляр с тем же именем из `sound_mouth_sync.launch` **не** запущен):
   ```bash
   rosrun sound_mouth_sync emotion_node.py \
     _emotion_cycle_enabled:=true \
     _emotion_cycle_interval_sec:=3.0
   ```

Доп. контекст: [doc/AI_CONTEXT.md](doc/AI_CONTEXT.md).

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

<a id="audio-playback-section"></a>

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

<a id="params-table"></a>

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
| `mouth_oled_startup_delay_sec` | `7.0` (YAML `hardware`) | Макс. секунд ожидания появления рта на I2C (`i2cdetect`, адрес из `i2c_address`); увеличить при медленном холодном старте модуля |
| `mouth_display_redraw_after_sec` | `0` (YAML `display`) | Повторная отрисовка рта через N с после старта; `8`–`10` при кратком «мусоре» на экране |
| `reassert_effective_topics_after_sec` | `0` (YAML `display`) | Повторная публикация `/mouth/effective_*` через N с |
| `emotion_cycle_enabled` | `false` (на `mouth_emotion_node`) | Демо-круг по `EMOTION_CYCLE_SEQUENCE`; см. [§ Демо-круг](#demo-emotion-cycle) |
| `emotion_cycle_interval_sec` | `3.0` | Интервал шага круга (сек) |

## Устранение неполадок

<a id="dual-oled"></a>

### Два OLED, перемычка I²C и полевой кейс «на рту как статус»

**Штатно** на одной шине I²C два **разных** 7-bit адреса; иначе оба чипа получают один и тот же трафик.

| I²C (7-bit) | Кто рисует | Содержимое |
|-------------|------------|------------|
| **0x3C** | `ainex_bringup` → `oled_display.py` | SSID, IP, CPU, MEM, … |
| **0x3D** | `sound_mouth_sync` → `mouth_display_node` | Эмоции, осциллограмма |

**Неисправность:** второй модуль оставлен на **0x3C** (или рот не отвечает на **0x3D**) → на «рту» видна та же статистика, что на системном экране. Это **не** баг топиков ROS. Долгое выключение **само по себе** адрес не «перепутывает»; чаще совпадение по времени — контакт, питание, холодный старт. Проверка: `sudo i2cdetect -y 1` — норма **и `3c`, и `3d`**.

**Железо — IIC ADDRESS SELECT:** на обратной стороне платы модуля SSD1306 зона с двумя позициями SMD-резистора; на шёлке часто **0x78** / **0x7A** (8-bit стиль) → на шине Pi **0x3C** / **0x3D**. Системный экран обычно оставляют в **0x78→0x3C**; **рот** переносят на **0x7A→0x3D**, чтобы совпало с `hardware.i2c_address` / `oled_i2c_address:=61`. После пайки снова `i2cdetect`.

На фото ниже — пример: синяя ПП, сверху 4-пиновый разъём (GND, VCC, SCL, SDA), снизу шлейф; **оранжевая отметка** — зона **IIC ADDRESS SELECT**; чёрный SMD-компонент — перемычка (на снимке в позиции **0x78**, т.е. **0x3C**; для рта нужна **0x7A**). Маркировка плат может отличаться.

<p align="center">
<img src="doc/images/oled_i2c_address_select_example.png" alt="Плата OLED: обратная сторона, IIC ADDRESS SELECT и перемычка 0x78/0x7A" width="560"/>
</p>

*Рис. Перемычка выбора I²C-адреса.* Доп.: [doc/ARCHITECTURE.md](doc/ARCHITECTURE.md), [doc/VISIO_SCHEME_OLED_JUMPER.md](doc/VISIO_SCHEME_OLED_JUMPER.md), [doc/SECOND_DISPLAY_ARCHITECTURE.md §8](doc/SECOND_DISPLAY_ARCHITECTURE.md#8-два-физических-oled-типичные-сбои-ssidip-на-рту-пропадание-0x3d).

**Софт не заменяет разные адреса**, но смягчает симптомы: env **`AINEX_STATS_PAUSE_ON_3C_UNLESS_3D=1`** для `oled_display.service` (не слать статус на 0x3C, пока на шине нет **0x3D**; см. §8.6 в документе выше). Если после старта на рту кратко «чужой» кадр при уже живом **0x3D** — в YAML `display` задайте **`mouth_display_redraw_after_sec`** / **`reassert_effective_topics_after_sec`** (см. [таблицу параметров](#params-table)); это не устраняет два модуля на **0x3C**.

#### «Вчера рот работал, сегодня на рту снова SSID/IP»

`oled_display.py` **никогда** не шлёт SSID/IP на **0x3D** — только на **0x3C**. Картинка статуса на физическом «рту» ⇒ этот модуль слушает **0x3C** вместе с системным **или** рот не ACK на **0x3D**.

1. **`sudo i2cdetect -y 1`** — нужны **3c** и **3d**; только **3c** → перемычка рта на **0x3D**, кабель, питание.
2. Лог **`mouth_display_node`**: после standup опрос шины до появления **0x3D**; лимит секунд и смысл тайм-аута — **`mouth_oled_startup_delay_sec`** ([таблица параметров](#params-table)); при «есть 3c, нет 3d» — **DIAGNOSTIC**. Нужен **`i2c-tools`**; при медленном холодном старте увеличьте лимит (например **12–15** с).
3. **`/mouth/mode` = oscillogram** дубль адреса **не** лечит.
4. Живой **`/audio/level`**, но «системная» картинка на OLED → сначала I²C, не PulseAudio: [doc/AUDIO_PLAYBACK.md](doc/AUDIO_PLAYBACK.md#осциллограмма-в-топиках-есть-но-на-экране-рта-не-та-картинка-ssidip).

Ещё: [doc/AI_CONTEXT.md](doc/AI_CONTEXT.md#troubleshooting-два-oled-дисплея), §8.7 в SECOND_DISPLAY (опрос шины при старте).

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

Сначала убедитесь, что звук заведён в тот же PulseAudio, что и `audio_capture_node` ([раздел «Воспроизведение звука»](#audio-playback-section) выше), и что на рту корректный I²C ([два OLED](#dual-oled)). Затем диагностика:

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

Полная настройка — в [«Воспроизведение звука»](#audio-playback-section) (блок «С хоста»). Быстрая проверка:

```bash
source $(rospack find sound_mouth_sync)/scripts/setup_host_audio.sh --check
```

Дополнительно: [doc/AUDIO_PLAYBACK.md](doc/AUDIO_PLAYBACK.md#воспроизведение-с-хоста-raspberry-pi).

### OLED-дисплей показывает перевёрнутое изображение

Параметр `rotate` в `config/sound_mouth_sync.yaml` секция `hardware`:
- `0` — нормальная ориентация
- `2` — поворот на 180° (по умолчанию, для перевёрнутого монтажа)
