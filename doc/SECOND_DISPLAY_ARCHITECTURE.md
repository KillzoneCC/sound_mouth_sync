# Архитектура ROS-пакета `sound_mouth_sync`

Пакет управляет **вторым дисплеем** робота Ainex (OLED SSD1306 128x64, I2C, область рта).

---

## 1. Поэтапный запуск (что происходит по шагам)

Команда:

```bash
roslaunch sound_mouth_sync sound_mouth_sync.launch
```

### Этап 0 — roslaunch загружает конфиг

```
roslaunch
  │
  ├─ rosparam load config/sound_mouth_sync.yaml   ← параметры в Parameter Server
  │
  ├─ Запускает НОДУ 0: mouth_emotion_node (хранит effective_mode / effective_emotion)
  ├─ Запускает НОДУ 1: mouth_display_node (рендер через mouth_emotion_render + I2C)
  └─ Запускает НОДУ 2: mouth_audio_capture_node
```

Обе ноды стартуют **параллельно** (roslaunch не гарантирует порядок). Но `display_node` готов сразу (рисует эмоцию), а `audio_capture` тратит 5-10 секунд на PulseAudio. Порядок не важен — они связаны только через **топики**.

### Этап 1 — `mouth_audio_capture_node` поднимает звук

```
mouth_audio_capture_node запустилась
  │
  ├─ 1.1  Запуск PulseAudio (pulseaudio --start)
  ├─ 1.2  Поиск USB-звуковой карты (/proc/asound/cards)
  │       Нет карты? → rospy.signal_shutdown(), нода умирает
  ├─ 1.3  Создание PA sink "usb_output" (module-alsa-sink)
  ├─ 1.4  Включение TCP:4713 (module-native-protocol-tcp)
  ├─ 1.5  Запись /etc/pulse/client.conf
  ├─ 1.6  Создание Publisher:
  │         /mouth/audio_wave  (Float32MultiArray)
  │         /audio/level       (Float32)
  ├─ 1.7  Запуск потока capture_loop:
  │         parec -d usb_output.monitor → PCM → downsample → publish
  └─ 1.8  rospy.spin() — ожидание shutdown
```

### Этап 2 — `mouth_display_node` готовит дисплей

```
mouth_display_node запустилась
  │
  ├─ 2.1  Чтение конфига (sms_config → YAML + rosparam)
  ├─ 2.2  Загрузка кастомных PNG из resources/emotions/
  ├─ 2.3  Инициализация OLED по I2C (luma.oled ssd1306)
  │       I2C недоступен? → продолжает без экрана (logwarn)
  ├─ 2.4  Создание Publisher:
  │         /mouth/current_mode    (String, latch)
  │         /mouth/current_emotion (String, latch)
  ├─ 2.5  Публикация начального состояния:
  │         current_mode / current_emotion — по `start_display_mode` и `default_emotion`
  │         (часто после старта oscillogram + neutral, см. launch/YAML)
  ├─ 2.6  Отрисовка начальной эмоции на OLED
  ├─ 2.7  Создание Subscriber (6 штук):
  │         /mouth/effective_mode     ← from emotion_node
  │         /mouth/effective_emotion  ← from emotion_node
  │         /mouth/audio_wave    ← от audio_capture
  │         /audio/level         ← от audio_capture
  │         /robot/posture       ← от joystick_control
  │         /robot/is_moving     ← от joystick_control
  ├─ 2.8  Запуск таймеров:
  │         auto_return_tick  (0.3 с) — возврат из osc в emotion при тишине
  │         on_idle_tick      (0.5 с) — проверка простоя → sleepy
  │         sleepy_anim_tick  (0.12 с) — анимация «курит» для sleepy
  └─ 2.9  rospy.spin()
```

### Этап 3 — рабочий цикл (обе ноды работают)

```
audio_capture (каждый чанк ~21 мс при 48000/1024):
  PCM → RMS + downsample → publish /mouth/audio_wave + /audio/level
       │                          │
       ▼                          ▼
display_node (колбэки):
  on_audio_wave() → слышно? → да → переключить на oscillogram, рисовать волну
                            → нет → ничего (таймер вернёт в emotion через 3 с)
  on_audio_level() → подстраховка для тихих файлов
  on_mode()        → ручное переключение emotion/oscillogram
  on_emotion()     → сменить эмоцию
  on_posture()     → падение? → принудительно emotion + fall_emotion
  on_moving()      → сбросить таймер простоя
  idle_tick()      → 120 с без активности? → sleepy
```

### Этап 4 — завершение (rosnode kill / Ctrl+C)

```
rospy.on_shutdown:
  display_node → рисует "neutral" на OLED → выход
  audio_capture → terminate parec → выход
```

---

## 2. Ноды

| # | ROS-имя | Скрипт | Что делает |
|---|---------|--------|------------|
| 0 | `mouth_emotion_node` | `emotion_node.py` | Хранит внешние команды эмоции/режима (`/mouth/mode`, `/mouth/emotion`) и публикует latched `effective_*` для display_node. |
| 1 | `mouth_display_node` | `display_node.py` | Рисует на OLED: эмоции или осциллограмму. Решает, что показать (приоритеты: падение > ручной режим > авто > простой). Публикует фактическое состояние. |
| 2 | `mouth_audio_capture_node` | `audio_capture_node.py` | Поднимает PulseAudio, захватывает весь системный звук через `parec`, публикует волну и уровень. |
| — | *(внешняя)* `joystick_control` | пакет `ainex_peripherals` | Публикует `/robot/posture` и `/robot/is_moving`. |
| — | *(внешние)* TTS, навигация и т.д. | другие пакеты | Публикуют `/mouth/mode` и `/mouth/emotion`. |

---

## 3. Топики

### 3.1 Публикация (Publisher)

| Топик | Тип | Откуда | Куда | Зачем |
|-------|-----|--------|------|-------|
| `/mouth/audio_wave` | `Float32MultiArray` | `audio_capture` | `display` | 128 точек волны для осциллограммы и определения «есть звук» |
| `/audio/level` | `Float32` | `audio_capture` | `display` + внешние | RMS 0..1, подстраховка для тихих файлов |
| `/mouth/current_mode` | `String` (latch) | `display` | внешние подписчики | Что сейчас на экране: `emotion` или `oscillogram` |
| `/mouth/current_emotion` | `String` (latch) | `display` | внешние подписчики | Какая эмоция реально нарисована |
| `/mouth/effective_mode` | `String` (latch) | `emotion_node` | `display` | Итоговая команда режима для дисплея |
| `/mouth/effective_emotion` | `String` (latch) | `emotion_node` | `display` | Итоговая пользовательская эмоция для дисплея |

### 3.2 Подписка (Subscriber)

| Топик | Тип | Откуда | Куда | Зачем |
|-------|-----|--------|------|-------|
| `/mouth/mode` | `String` | внешние ноды | `display` | Команда: `"emotion"` или `"oscillogram"` |
| `/mouth/emotion` | `String` | внешние ноды | `display` | Команда: имя эмоции (`happy`, `sad`, ...) |
| `/mouth/mode` | `String` | внешние ноды | `emotion_node` | Внешняя команда режима |
| `/mouth/emotion` | `String` | внешние ноды | `emotion_node` | Внешняя команда эмоции |
| `/mouth/effective_mode` | `String` | `emotion_node` | `display` | Эффективная команда режима |
| `/mouth/effective_emotion` | `String` | `emotion_node` | `display` | Эффективная команда эмоции |
| `/mouth/audio_wave` | `Float32MultiArray` | `audio_capture` | `display` | Данные волны |
| `/audio/level` | `Float32` | `audio_capture` | `display` | Доп. проверка уровня |
| `/robot/posture` | `String` | `joystick_control` | `display` | `stand` / `fall_*` — приоритет падения |
| `/robot/is_moving` | `Bool` | `joystick_control` | `display` | Ходьба сбрасывает таймер простоя |

---

## 4. Сервисы (Services)

### Факт: в пакете `sound_mouth_sync` сервисов **нет**

В коде нет ни одного `rospy.Service()` или `rospy.ServiceProxy()`. Все решения принимаются **внутри `display_node`** через колбэки топиков и таймеры.

### Где нужны сервисы по смыслу — «вопрос-ответ»

Ниже перечислены логические точки, которые **работают как вопрос-ответ** внутри кода и **могут быть вынесены в ROS Service**, если понадобится синхронный запрос от другой ноды:

| Логический «сервис» | Вопрос | Ответ | Где сейчас в коде | Возможный тип srv |
|---------------------|--------|-------|--------------------|-------------------|
| **Слышно ли звук?** | Дать 128 точек волны или RMS | `true` / `false` | `mouth_audio_gates.waveform_is_audible()` внутри `on_audio_wave()` | `std_srvs/SetBool` или свой `AudioGate.srv` |
| **Какой сейчас режим?** | (пустой запрос) | `"emotion"` или `"oscillogram"` | Читается из latched `/mouth/current_mode` | `std_srvs/Trigger` → message |
| **Какая эмоция на экране?** | (пустой запрос) | `"happy"`, `"sleepy"`, ... | Читается из latched `/mouth/current_emotion` | `std_srvs/Trigger` → message |
| **Робот упал?** | (пустой запрос) | `true` / `false` | `_robot_is_fallen()` в `display_node` | `std_srvs/Trigger` |
| **Установить эмоцию** | имя эмоции | `success` / `error` | Сейчас через топик `/mouth/emotion` | Свой `SetEmotion.srv` |
| **Переключить режим** | `"emotion"` или `"oscillogram"` | `success` | Сейчас через топик `/mouth/mode` | Свой `SetMode.srv` |

Сейчас все эти «вопросы» решаются **асинхронно** (топики + latch). Это нормально для данного пакета. Если вам нужен **синхронный** запрос-ответ (например, другая нода хочет **дождаться** результата), то можно добавить сервис.

---

## 5. Блок-схема архитектуры

Три уровня: **5.1** — ноды, важные топики, циклы запуска; **5.2** — логика `display_node` (ромбы = «вопрос-ответ» в коде); **5.3** — всё связано одной схемой. **ROS Service** в пакете не объявлены (§4); на рисунках это отдельная пометка, а не «лишняя нода».

### 5.1 Запуск, ноды, топики и циклы

```mermaid
flowchart TB
  L(["roslaunch<br/>sound_mouth_sync.launch"])
  Y["rosparam ←<br/>config/sound_mouth_sync.yaml"]
  L --> Y

  Y --> N1["НОДА 1<br/>mouth_audio_capture_node"]
  Y --> N2["НОДА 2<br/>mouth_display_node"]

  PA{"PulseAudio<br/>запустился?"}
  USB{"USB-карта<br/>найдена?"}
  SETUP["Sink usb_output + TCP:4713<br/>parec на .monitor"]
  CHUNK["Чанк PCM"]
  RMS["RMS + downsample<br/>128 точек"]
  TW["ТОПИК pub<br/>/mouth/audio_wave<br/>Float32MultiArray"]
  TL["ТОПИК pub<br/>/audio/level<br/>Float32"]
  AGAIN["следующий чанк"]

  N1 --> PA
  PA -->|нет| DIE["shutdown"]
  PA -->|да| USB
  USB -->|нет| DIE
  USB -->|да| SETUP --> CHUNK --> RMS
  RMS --> TW
  RMS --> TL
  TW --> AGAIN
  TL --> AGAIN
  AGAIN --> CHUNK

  CFG["Конфиг + PNG<br/>I2C OLED?"]
  PUB0["pub latch:<br/>current_mode=emotion<br/>current_emotion=…"]
  SUB["sub 6 топиков:<br/>/mouth/mode, /mouth/emotion<br/>/mouth/audio_wave, /audio/level<br/>/robot/posture, /robot/is_moving"]
  TM["таймеры:<br/>silence_return, idle_sleep,<br/>sleepy_anim"]
  SPIN["rospy.spin"]

  N2 --> CFG --> PUB0 --> SUB --> TM --> SPIN

  TW -.->|sub| SUB
  TL -.->|sub| SUB

  SRV1["Сервисы ROS:<br/>в этом пакете нет<br/>(логика в колбэках)"]
  style SRV1 fill:#fff6ed,stroke:#d35400,color:#8b4513,stroke-dasharray: 5 5
```

### 5.2 Логика display_node (ветвления)

```mermaid
flowchart TB
  START(["Пришло сообщение<br/>в колбэк"])

  FALL{"Робот упал?<br/>/robot/posture = fall_*"}
  FALL_YES["Да → эмоция падения<br/>osc запрещена"]
  ALREADY{"Уже в режиме<br/>oscillogram?"}
  DRAW_WAVE["Да → рисовать волну<br/>на OLED"]
  SILENCE{"Тишина ≥<br/>silence_return_sec?"}
  BACK_EMO["Да → вернуть emotion<br/>снова слушаем топики"]
  STAY_OSC["Нет → оставить osc"]
  AUTO{"auto_mode включён<br/>и сейчас emotion?"}
  AUDIBLE{"Слышно?<br/>mouth_audio_gates"}
  TO_OSC["Да → oscillogram<br/>pub current_*"]
  STAY_EMO["Нет → остаться в emotion"]
  IDLE{"Простой ≥<br/>idle_sleep_sec?"}
  SLEEPY["Да → sleepy"]

  START --> FALL
  FALL -->|да| FALL_YES
  FALL -->|нет| ALREADY
  ALREADY -->|да| DRAW_WAVE
  DRAW_WAVE --> SILENCE
  SILENCE -->|да| BACK_EMO
  SILENCE -->|нет| STAY_OSC
  ALREADY -->|нет| AUTO
  AUTO -->|да| AUDIBLE
  AUDIBLE -->|да| TO_OSC
  AUDIBLE -->|нет| STAY_EMO
  AUTO -->|нет| IDLE
  IDLE -->|да| SLEEPY
  IDLE -->|нет| STAY_EMO
```

### 5.3 Одна схема: ноды → топики → логика → выход

Пунктир — **нет объявленных srv**; ромбы внутри `display_node` = вопрос-ответ в коде (§4).

```mermaid
flowchart TB
  LAUNCH(["roslaunch"]) --> YAML["YAML → rosparam"]

  subgraph cap["НОДА: mouth_audio_capture_node"]
    PA2{"PA OK?"}
    USB2{"USB OK?"}
    PREP2["sink + parec"]
    LOOP2["цикл: PCM → RMS → pub"]
    PA2 -->|нет| D2["shutdown"]
    PA2 -->|да| USB2
    USB2 -->|нет| D2
    USB2 -->|да| PREP2 --> LOOP2
    LOOP2 --> LOOP2
  end

  YAML --> cap

  TW2["/mouth/audio_wave<br/>Float32MultiArray"]
  TL2["/audio/level<br/>Float32"]
  LOOP2 --> TW2
  LOOP2 --> TL2

  subgraph ext["Внешние ноды"]
    JC2["joystick_control"]
    OTH["другие пакеты<br/>(TTS, телеоп, …)"]
  end

  TM2["/mouth/mode String"]
  TE2["/mouth/emotion String"]
  TP2["/robot/posture String"]
  TMV2["/robot/is_moving Bool"]

  OTH --> TM2
  OTH --> TE2
  JC2 --> TP2
  JC2 --> TMV2

  subgraph dis["НОДА: mouth_display_node"]
    ST3(["колбэк по<br/>любому из sub"])
    F3{"Упал?<br/>fall_*?"}
    FY3["fall-эмоция<br/>osc off"]
    A3{"Уже osc?"}
    W3["волна OLED"]
    S3{"Тишина ≥<br/>silence_return?"}
    B3["→ emotion"]
    O3["→ osc"]
    M3{"auto + emotion?"}
    H3{"Слышно?<br/>gates"}
    G3["→ osc + pub"]
    E3["→ emotion"]
    I3{"Простой ≥<br/>idle_sleep?"}
    Z3["sleepy"]
    OLED2["OLED I2C<br/>SSD1306"]

    ST3 --> F3
    F3 -->|да| FY3
    F3 -->|нет| A3
    A3 -->|да| W3 --> S3
    S3 -->|да| B3
    S3 -->|нет| O3
    A3 -->|нет| M3
    M3 -->|да| H3
    H3 -->|да| G3
    H3 -->|нет| E3
    M3 -->|нет| I3
    I3 -->|да| Z3
    I3 -->|нет| E3

    FY3 --> OLED2
    W3 --> OLED2
    G3 --> OLED2
    Z3 --> OLED2
    E3 --> OLED2
    B3 --> OLED2
    O3 --> OLED2
  end

  YAML --> dis
  TW2 -.->|sub → колбэк| ST3
  TL2 -.->|sub → колбэк| ST3
  TM2 -.->|sub → колбэк| ST3
  TE2 -.->|sub → колбэк| ST3
  TP2 -.->|sub → колбэк| ST3
  TMV2 -.->|sub → колбэк| ST3

  CM["/mouth/current_mode<br/>String latch"]
  CE["/mouth/current_emotion<br/>String latch"]
  FY3 -->|pub| CM
  FY3 -->|pub| CE
  G3 -->|pub| CM
  B3 -->|pub| CM
  B3 -->|pub| CE
  Z3 -->|pub| CE

  CM -->|sub| OTH
  CE -->|sub| OTH
  TL2 -.->|sub опционально| OTH

  SRV2["ROS Service:<br/>в пакете не объявлены"]
  style SRV2 fill:#fff6ed,stroke:#d35400,color:#8b4513,stroke-dasharray: 5 5
```

### 5.4 SVG-файл

Файл **[`mouth_block_scheme_refined.svg`](mouth_block_scheme_refined.svg)** — отдельный чертёж в SVG (можно упростить вручную под презентацию).

---

## 6. Сводная таблица всех связей

| От | Связь | К | Тип | Направление |
|----|-------|---|-----|-------------|
| `audio_capture` | `/mouth/audio_wave` | `display` | Topic (pub→sub) | → |
| `audio_capture` | `/audio/level` | `display` + внешние | Topic (pub→sub) | → |
| внешние | `/mouth/mode` | `display` | Topic (pub→sub) | → |
| внешние | `/mouth/emotion` | `display` | Topic (pub→sub) | → |
| `joystick_control` | `/robot/posture` | `display` | Topic (pub→sub) | → |
| `joystick_control` | `/robot/is_moving` | `display` | Topic (pub→sub) | → |
| `display` | `/mouth/current_mode` | внешние | Topic (pub→sub, latch) | → |
| `display` | `/mouth/current_emotion` | внешние | Topic (pub→sub, latch) | → |
| `display` | I2C | OLED SSD1306 | Аппаратная шина | → |
| *(нет)* | *(нет)* | *(нет)* | Service | Не используется (см. §4) |

---

## 7. Связанные материалы

- [ARCHITECTURE.md](ARCHITECTURE.md) — PulseAudio, файловая структура, блок «вчера ОК / утром SSID на рту».
- [README.md](../README.md) — запуск, параметры, примеры `rostopic`, чеклист полевого симптома.
- [AI_CONTEXT.md](AI_CONTEXT.md) — правила ИИ-агента, troubleshooting двух OLED.
- [AUDIO_PLAYBACK.md](AUDIO_PLAYBACK.md) — если осциллограмма в топиках есть, а на рту «чужая» картинка.

---

## 8. Два физических OLED: типичные сбои (SSID/IP на «рту», пропадание 0x3D)

Ниже зафиксированы **причины уровня «архитектура + железо»** и связанное **поведение `mouth_display_node`**. Симптомы совпадают с полевым кейсом: на экране во рту видна **системная** картинка (как на 0x3C), затем «рот» гаснет и **в шине пропадает адрес 0x3D**.

### 8.0 Взаимодействие двух модулей и адресов (кратко для оператора)

| Кто пишет | I2C (7-bit) | Что рисуется | Пакет / файл |
|-----------|-------------|--------------|--------------|
| Системный статус | **0x3C** | SSID, IP, CPU, MEM, DISK, BAT | `ainex_bringup` → `oled_display.py` |
| Рот | **0x3D** | Эмоции, осциллограмма | `sound_mouth_sync` → `display_node.py` |

Оба модуля висят на **одной** шине I²C. Контроллер SSD1306 **слушает тот адрес, который задан перемычкой ADDR на плате** (не «назначается» ROS при каждой загрузке). Если физически **два** модуля с одинаковой перемычкой (**оба 0x3C**), то **любая** транзакция на **0x3C** выполняется **обоими** чипами — на «рту» будет та же картинка, что и на системном экране. ПО **не** направляет пиксели статистики на **0x3D**: `oled_display.py` при активной разработке **запрещает** целевой адрес **0x3D** для статистики (`RuntimeError` при `AINEX_INFO_OLED_I2C_ADDR=0x3D`).

**Миф:** «робот долго не включали — из‑за этого на 3d появилось то же, что на 3c». **Реальность:** длительное выключение **само по себе** не дублирует I2C-адреса. Совпадение по времени возможно (окисление, шлейф, краткое исчезновение 0x3D на шине, ошибочная перемычка), но **механизм** — адресация и физика шины, а не календарь простоя. Полное «чтобы больше не повторилось» без проверки железа **не гарантирует** ни ПО, ни документация: при возврате к двум модулям на **0x3C** симптом вернётся.

### 8.1 Ожидаемое разделение (контракт)

| Адрес I2C (bus 1) | Физический модуль | Процесс / пакет |
|-------------------|-------------------|-----------------|
| **0x3C** | Статус: SSID, IP, CPU, MEM, DISK, BAT | `ainex_bringup/scripts/oled_display.py` (часто `oled_display.service`) |
| **0x3D** | Рот: эмоции, осциллограмма | `sound_mouth_sync` → `mouth_display_node` (`display_node.py`) |

`oled_display.py` в актуальной логике **всегда** рисует статистику на **0x3C** (переменная `AINEX_INFO_OLED_I2C_ADDR=0x3D` приводит к **RuntimeError** при старте — целенаправленный запрет писать статус на рот).

### 8.2 Почему картинка «как с первого дисплея (0x3C)» может появиться на втором (ожидаемый рот / 0x3D)

ПО **программным** каналам `oled_display` на 0x3D не должен писать (см. выше). Значит, эффект почти всегда объясняется **железом и топологией I2C**:

1. **Дублирующий адрес 0x3C на двух модулях**  
   Если модуль «рта» по перемычке/пайке оставлен на **0x3C** (как системный), оба SSD1306 слушают **один и тот же** 7-bit адрес. Трафик `oled_display.py` → 0x3C одновременно попадает на **оба** контроллера: на физически втором экране вы увидите **ту же** статистику, что и на первом. Это не «копирование через ROS», а **общая шина + один адрес**.

2. **Путаница физических разъёмов**  
   Два одинаковых кабеля: системный скрипт обновляет тот модуль, который вы считаете «ртом», если он подключён к той же цепочке, что и 0x3C (редко, но проверяется перестановкой/маркировкой).

3. **Устаревшая среда запуска**  
   Старый `oled_display` или unit-файл с окружением, форсирующим запись статуса на 0x3D (в текущем дереве это блокируется явно). Имеет смысл сверить `oled_display.service` и отсутствие `AINEX_INFO_OLED_I2C_ADDR=0x3D`.

**Практическая проверка при повторении:** `i2cdetect -y 1` — должны быть **и 3c, и 3d**. Если виден только **один** адрес `3c`, а «рот» дублирует статус — с высокой вероятностью **оба модуля на 0x3C**; рот нужно перевести на **0x3D** (перемычка/пайка по даташиту модуля).

### 8.3 Почему второй дисплей «потух» и пропал с шины (нет строки `3d` в `i2cdetect`)

Если устройство **не ACK**-ит на 0x3D, **ПО не может «убрать» его с шины** — отсутствие в `i2cdetect` означает, что контроллер OLED **не отвечает** (или не тот адрес).

Типичные причины (от более частых к более редким):

1. **Питание / кабель / разъём** модуля рта: просадка, окисление, отошёл FPC/шлейф после вибраций. Симптом: сначала артефакты/гаснет подсветка, затем полное молчание на I2C.

2. **Обрыв или КЗ линий SDA/SCL** только к второму модулю: первый (0x3C) остаётся в `i2cdetect`, второй исчезает.

3. **Повреждение чипа SSD1306** или его обвязки (ESD, переполюсовка при ремонте).

4. **Смена адреса** (оборвана перемычка ADDR): модуль мог бы откликаться не на 0x3D — тогда искать другой адрес в `i2cdetect` (осторожно: не делать выводов без даташита модуля).

**Важно:** пока **нет 0x3D**, `mouth_display_node` может работать как нода (топики), но **не отрисует** рот: в логе будет предупреждение, что OLED на I2C 0x3D недоступен; периодические повторные попытки открытия — в коде `display_node.py`.

### 8.4 Как вернуть осциллограмму и эмоции на второй экран (порядок действий на роботе)

Цель: на шине снова **0x3D**, `mouth_display_node` открывает устройство, `mouth_audio_capture_node` публикует волну.

1. **Аппаратная проверка:** питание 3.3 V/логики модуля рта, кабель, перемычка адреса **0x3D**, отсутствие КЗ на SDA/SCL. После ремонта снова `i2cdetect -y 1` — должны быть **3c** и **3d**.

2. **Права:** пользователь в группе `i2c` (если раньше работало без sudo — обычно уже настроено).

3. **Перезапуск стека ROS, который поднимает рот и осциллограмму** (на роботе с systemd):
   - полный bringup:  
     `sudo systemctl restart start_app_node.service`  
     (см. комментарии в `ainex_bringup/launch/bringup.launch`: тот же unit вызывает `roslaunch ainex_bringup bringup.launch`).
   - при необходимости отдельно системный OLED:  
     `sudo systemctl restart oled_display.service`

4. **Проверка нод:**  
   `rosnode list | grep mouth` — ожидаются `mouth_emotion_node`, `mouth_display_node`, `mouth_audio_capture_node`.

5. **Проверка осциллограммы:** при наличии USB-звука и PulseAudio — `rostopic hz /mouth/audio_wave` и краткий `paplay` (см. [AUDIO_PLAYBACK.md](AUDIO_PLAYBACK.md)).

6. **Если снова дублируется статус на рту:** не менять код в первую очередь — проверить **два разных I2C-адреса** на двух модулях и кабель к «рту».

### 8.5 Что было сделано при разборе в среде разработки (лог шагов)

1. Прочитаны `doc/ARCHITECTURE.md`, `doc/SECOND_DISPLAY_ARCHITECTURE.md`, `doc/AI_CONTEXT.md`, `docs/PROJECT-CONTRACT.md`, `README.md` — восстановлена схема нод и адресов 0x3C / 0x3D.  
2. Открыт `ainex_bringup/launch/bringup.launch` — подтверждён include `sound_mouth_sync.launch` и аргументы `mouth_*`.  
3. Прочитан `ainex_bringup/scripts/oled_display.py` — подтверждено: статистика только на **0x3C**, защита от `AINEX_INFO_OLED_I2C_ADDR=0x3D`.  
4. Выполнен `i2cdetect -y 1` в текущем контейнере/хосте среды Cursor: видны **0x29** и **0x3C**, **0x3D отсутствует** — согласуется с вашим сканом (второй дисплей с шины пропал).  
5. Проверка `systemctl` в этой среде: **systemd не является PID 1** — полноценный «презапуск» `start_app_node.service` здесь недоступен; его нужно выполнять **на самом роботе** (Raspberry Pi с установленным unit).  
6. Попытка `rosnode list` после `source devel/setup.bash` в этой среде: цепочка `setup.bash` ссылается на отсутствующий путь `sound_mouth_sync/setup.sh` — **ROS из этого workspace в контейнере не поднят**; проверку нод выполните на роботе после `source` рабочего `devel/setup.bash`.

### 8.6 Программные меры (не заменяют разный I2C-адрес)

**Жёсткое правило:** пока оба SSD1306 на шине имеют **один и тот же** адрес, **нельзя** одновременно показывать на них **разный** контент — шина адресует кадр целиком. Ни ROS-топики, ни «переключение на осциллограмму» это не обходят.

Что сделано в коде для **смягчения** типичных сценариев (автозапуск `ainex_bringup` + `sound_mouth_sync` **без смены** `bringup.launch`):

| Механизм | Где | Назначение |
|----------|-----|------------|
| **`AINEX_STATS_PAUSE_ON_3C_UNLESS_3D=1`** | env для `oled_display.py` / `oled_display.service` | Пока `i2cdetect` **не видит** устройство на **0x3D**, **не** отправлять SSID/IP/CPU на **0x3C**. Снижает риск «залить» статусом и второй модуль, если он ошибочно на **0x3C**. Системный экран остаётся пустым (после стартового blank), пока рот не появится на шине как **0x3D**. Не действует вместе с `AINEX_INFO_OLED_ALLOW_WITHOUT_3D=1` (лабораторный режим без второго OLED). |
| **`mouth_display_redraw_after_sec`** | `config/sound_mouth_sync.yaml` → секция `display` | Через N секунд после старта `mouth_display_node` **один раз** перерисовать текущий режим (эмоция или осциллограмма). Имеет смысл **8–10** с, если после включения на рту кратко «мелькает» чужой кадр. **Не устраняет** дубль адреса. |
| **`reassert_effective_topics_after_sec`** | тот же YAML, `display` | Через N с повторно опубликовать защёлкнутые `/mouth/effective_mode` и `/mouth/effective_emotion` (тот же текст), чтобы подписчик `display_node` гарантированно получил режим после позднего старта. |

**Постоянный адрес рта — 0x3D:** задаётся **перемычкой ADDR на модуле** и дублируется в `hardware.i2c_address` / launch `oled_i2c_address:=61`. Менять только ПО без железа **нельзя**, если нужны два независимых изображения.

**Включение паузы статуса на роботе (systemd):**

```bash
sudo systemctl edit oled_display.service
```

В открывшемся фрагменте:

```ini
[Service]
Environment=AINEX_STATS_PAUSE_ON_3C_UNLESS_3D=1
```

Затем `sudo systemctl daemon-reload && sudo systemctl restart oled_display.service`.

### 8.7 ПО `sound_mouth_sync`: старт рта после подъёма и опрос `0x3D`

**Полевой сценарий:** вечером рот и осциллограмма корректны, утром после включения снова дубль статистики на «рту» или рот «не ожил».

**Что сделано в коде** (`scripts/display_node.py`), без нарушения контракта двух адресов:

1. **Порядок:** сначала ожидание **`init_pose/init_finish`** (`_wait_for_robot_standup`), **затем** подготовка к открытию OLED рта (раньше фиксированная пауза выполнялась **до** standup и не помогала холодному появлению устройства на шине).
2. **Опрос шины:** в течение не более **`hardware.mouth_oled_startup_delay_sec`** секунд (YAML) нода через **`i2cdetect`** ждёт появления адреса рта (**по умолчанию 0x3D**). Если адрес уже есть — открытие дисплея **раньше**, чем по истечении тайм-аута.
3. **Диагностика:** если за отведённое время **0x3D** так и не появился, а **0x3C** виден — в лог пишется **`DIAGNOSTIC`** с текстом о типичном случае «оба модуля на 0x3C» и напоминанием проверить перемычку рта и `i2cdetect`.
4. Если **`i2cdetect`** недоступен (нет `i2c-tools`), поведение откатывается к **слепой** паузе на `mouth_oled_startup_delay_sec` с предупреждением в логе.

**Повтор симптома:** по-прежнему в первую очередь **железо и адреса** (§8.2–8.4); при медленном включении модуля рта увеличьте **`mouth_oled_startup_delay_sec`** (например 12–15). Команды ROS **`/mouth/mode`, `/mouth/emotion`** не заменяют разные I2C-адреса на двух платах.

См. также: [ARCHITECTURE.md](ARCHITECTURE.md) (раздел «Симптом вчера ОК…»), [README.md](../README.md), [AI_CONTEXT.md](AI_CONTEXT.md).
