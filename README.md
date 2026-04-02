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

Слой хранения команд эмоций/режима (**`emotion_node`**) включён в `sound_mouth_sync.launch`:

- внешний API не меняется: **`/mouth/mode`**, **`/mouth/emotion`**
- **`emotion_node`** публикует с защёлкой **`/mouth/effective_mode`**, **`/mouth/effective_emotion`**
- **`display_node`** подписан на **`effective_*`** (не на «сырые» `/mouth/mode` и `/mouth/emotion` напрямую) и рисует на **I2C 0x3D**; публикует фактическое состояние в **`/mouth/current_*`**

### Круг эмоций в архитектуре (как это работает)

Внутри **`emotion_node`** опционально включается **таймер**: раз в **`~emotion_cycle_interval_sec`** (например, 3 с) следующее имя из списка **`EMOTION_CYCLE_SEQUENCE`** в `mouth_emotion_render.py` записывается в **`current_emotion`** и снова публикуется на **`/mouth/effective_emotion`**. **`display_node`** получает то же сообщение и обновляет кадр на OLED **0x3D**. Порядок имён в коде фиксирован (нейтральная → happy → … → **cute** → … → `cat` → снова с начала).

- При **`_emotion_cycle_enabled:=true`** при старте ноды эффективный режим принудительно **`emotion`**, чтобы не слать отдельно `/mouth/mode` перед демо.
- Пока на **`/mouth/effective_mode`** висит **`oscillogram`**, счётчик круга **не увеличивается** (демо «заморожено», пока режим снова не `emotion`).
- Включённый круг **перезаписывает** то, что могли бы задать вручную через `/mouth/emotion`, до следующего тика или смены режима.

Запуск **только** `emotion_node` с кругом (без второго экземпляра с тем же именем; штатный `roslaunch` с `mouth_emotion_node` при этом не должен быть запущен):

```bash
rosrun sound_mouth_sync emotion_node.py \
  _emotion_cycle_enabled:=true \
  _emotion_cycle_interval_sec:=3.0
```

В типичном сценарии рядом поднимают **`display_node`** и **`audio_capture_node`** (или целиком `roslaunch sound_mouth_sync sound_mouth_sync.launch` с параметрами круга в XML, см. раздел «Демо-круг эмоций» ниже).

Подробная схема и ноды: [doc/ARCHITECTURE.md](doc/ARCHITECTURE.md)

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

Команды идут на **`/mouth/emotion`** → `emotion_node` → **`/mouth/effective_emotion`** → `display_node` → физический **рот SSD1306 по I2C 0x3D**. Сначала включите режим эмоций (если сейчас осциллограмма):

```bash
rostopic pub -1 /mouth/mode std_msgs/String "data: 'emotion'"
```

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

### Демо-круг эмоций (`emotion_node`)

Опционально `mouth_emotion_node` может **автоматически переключать** эмоции по фиксированному списку (удобно для витрины без отдельных публикаций). Порядок задан в коде `scripts/mouth_emotion_render.py` → **`EMOTION_CYCLE_SEQUENCE`**:

`neutral` → `happy` → `sad` → `angry` → `surprised` → `excited` → `love` → **`cute`** → `confused` → `scared` → `bored` → `calm` → `disgusted` → `tired` → `sleepy` → `sleep` → `cat` → (снова с начала).

| Параметр (на `mouth_emotion_node`) | По умолчанию | Смысл |
|-----------------------------------|---------------|--------|
| `~emotion_cycle_enabled` | `false` | Включить автоматический круг |
| `~emotion_cycle_interval_sec` | `3.0` | Пауза между шагами (сек), минимум ~0.3 |

Поведение:

- При **`emotion_cycle_enabled:=true`** при старте эффективный режим принудительно **`emotion`**, чтобы круг был виден на **0x3D** без отдельной команды `/mouth/mode` (один терминал для демо).
- Шаг круга выполняется **только пока эффективный режим — `emotion`**; на осциллограмме индекс круга **не продвигается** (см. `emotion_node.py`).
- При **`emotion_cycle_enabled:=true`** таймер может **перебивать** ручные `rostopic pub /mouth/emotion`; для стабильного ручного переключения держите цикл **выключенным** (значение по умолчанию или явно `_emotion_cycle_enabled:=false`).
- Если при включённом круге нужен старт сразу в эмоциях одним процессом, используйте те же приватные параметры при запуске `emotion_node` (см. ниже), не поднимая **второй** экземпляр ноды поверх `roslaunch`.

**Включение круга** (выберите один вариант):

1. **Свой launch** — в узле `mouth_emotion_node` добавьте параметры:
   ```xml
   <param name="emotion_cycle_enabled" value="true"/>
   <param name="emotion_cycle_interval_sec" value="3.0"/>
   ```
2. **Отдельный запуск только `emotion_node`** (только если штатный `mouth_emotion_node` из `sound_mouth_sync.launch` **не** запущен — иначе будет конфликт имён):
   ```bash
   rosrun sound_mouth_sync emotion_node.py \
     _emotion_cycle_enabled:=true \
     _emotion_cycle_interval_sec:=3.0
   ```

Подробности: [doc/AI_CONTEXT.md](doc/AI_CONTEXT.md) (строка про `emotion_node`).

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
| `mouth_oled_startup_delay_sec` | `7.0` (YAML `hardware`) | Макс. секунд ожидания появления рта на I2C (`i2cdetect`, адрес из `i2c_address`); увеличить при медленном холодном старте модуля |
| `mouth_display_redraw_after_sec` | `0` (YAML `display`) | Повторная отрисовка рта через N с после старта; `8`–`10` при кратком «мусоре» на экране |
| `reassert_effective_topics_after_sec` | `0` (YAML `display`) | Повторная публикация `/mouth/effective_*` через N с |
| `emotion_cycle_enabled` | `false` (на `mouth_emotion_node`) | Демо-круг по `EMOTION_CYCLE_SEQUENCE`; см. раздел выше |
| `emotion_cycle_interval_sec` | `3.0` | Интервал шага круга (сек) |

## Устранение неполадок

### Два OLED: системный (0x3C) и рот (0x3D)

**Важно:** в **штатной** конфигурации у вас **два разных** адреса: **0x3C** (статус) и **0x3D** (рот) — так задумано в железе и в ПО. Фраза «оба на одном адресе» относится только к **режиму неисправности**: если второй модуль ошибочно тоже прошит/перемычкой на **0x3C**, оба чипа физически слушают **один** адрес — тогда на обоих экранах окажется одна и та же картинка статуса. Либо модуль рта **не отвечает** на **0x3D** (контакт, питание, обрыв). Долгое выключение само по себе обычно не объясняет дубль; проверка: `i2cdetect -y 1` — норма: **и `3c`, и `3d`**. При симптоме см. ниже и [doc/SECOND_DISPLAY_ARCHITECTURE.md](doc/SECOND_DISPLAY_ARCHITECTURE.md) (§8–8.6).

На одной I2C-шине у SSD1306 **должны быть разные 7-bit адреса**: обычно **0x3C** — статус (SSID, IP, …) через `ainex_bringup` → `oled_display.py`, **0x3D** — эмоции и осциллограмма через `mouth_display_node`. Адрес второго модуля задаётся **перемычкой на плате дисплея** (см. даташит модуля), не «прошивкой» в ROS.

Если **оба** модуля оставлены на **0x3C**, любая отрисовка статуса на 0x3C попадёт **на оба** экрана — это не баг топиков. Надёжное решение: перевести модуль рта на **0x3D** и убедиться, что `i2cdetect -y 1` показывает **и `3c`, и `3d`**.

**Пока правите железо**, можно снизить дублирование статуса на «рту»:

- Переменная окружения **`AINEX_STATS_PAUSE_ON_3C_UNLESS_3D=1`** для `oled_display.service`: не слать статистику на 0x3C, пока на шине **нет** ответа на **0x3D** (см. комментарий в `ainex_bringup/service/oled_display.service` и раздел **8.6** в [doc/SECOND_DISPLAY_ARCHITECTURE.md](doc/SECOND_DISPLAY_ARCHITECTURE.md)).

**После старта снова показать осциллограмму/эмоцию на рту** (если кратко мелькнул чужой кадр, а **0x3D** уже есть):

- В `config/sound_mouth_sync.yaml` в секции `display` задайте, например, `mouth_display_redraw_after_sec: 9.0` и при необходимости `reassert_effective_topics_after_sec: 9.0`, затем перезапустите ноды рта (или весь `bringup`). Это **не заменяет** исправление адреса при двух модулях на 0x3C.

Подробнее: [doc/SECOND_DISPLAY_ARCHITECTURE.md §8](doc/SECOND_DISPLAY_ARCHITECTURE.md#8-два-физических-oled-типичные-сбои-ssidip-на-рту-пропадание-0x3d), §8.7 (старт `display_node` и опрос шины).

### «Вчера рот и осциллограмма работали, сегодня на рту снова SSID/IP»

Это **частый полевой вопрос**: кажется, что «адреса путаются после ночи или долгого выключения».

**Что на самом деле происходит**

- Программа статуса (`oled_display.py` в `ainex_bringup`) **никогда не шлёт** SSID/IP на **0x3D** — только на **0x3C**.
- Если вы видите **ту же** текстовую статистику на экране, который считаете «ртом», физический второй модуль **принимает записи на 0x3C** (типично **оба** SSD1306 с перемычкой **0x3C**) **или** модуль рта **не отвечает на 0x3D**, а на втором экране остаётся только картинка с общей шины **0x3C**.
- **Долгое выключение** само по себе не является механизмом «переназначения адреса» в ROS; реже совпадение по времени связано с **контактом, питанием, холодным стартом** (модуль рта позже появляется на шине или пропадает).

**Что сделать по шагам (не вслепую менять топики)**

1. **`sudo i2cdetect -y 1`** — должны быть **и `3c`, и `3d`**. Только `3c` при «дубле на рту» → железо: перемычка **ADDR** на плате рта = **0x3D**, кабель, питание.
2. Лог **`mouth_display_node`**: после подъёма робота нода **ждёт появления 0x3D** на шине (см. `hardware.mouth_oled_startup_delay_sec` в YAML — это **максимум** секунд ожидания). При тайм-уте и ситуации «есть 0x3C, нет 0x3D» в лог пишется **DIAGNOSTIC** с подсказкой.
3. Если рот на холодном старте поднимается долго — увеличьте **`mouth_oled_startup_delay_sec`** (например **12–15**). Для опроса нужен пакет **`i2c-tools`**.
4. Команды **`/mouth/mode` = oscillogram** не устраняют дубль **I2C-адреса** на двух модулях.
5. Если **`/audio/level`** реагирует, а на OLED всё равно «системная» картинка — это путь **дисплея**, не PulseAudio: см. [doc/AUDIO_PLAYBACK.md](doc/AUDIO_PLAYBACK.md#осциллограмма-в-топиках-есть-но-на-экране-рта-не-та-картинка-ssidip).

Полная схема взаимодействия модулей и адресов: [doc/ARCHITECTURE.md](doc/ARCHITECTURE.md), [doc/AI_CONTEXT.md](doc/AI_CONTEXT.md#troubleshooting-два-oled-дисплея).

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
