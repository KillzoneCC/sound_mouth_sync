## 🚦 Статус проекта
- Текущий этап пайплайна: Концепция
- Светофор: 🟡 (контракт создан из документации; требуется подтверждение)
- Версия: v0.1.0

## 📋 Контракт Разработки
### Функциональные требования
- Пакет `sound_mouth_sync` управляет OLED-дисплеем SSD1306 128x64 (I2C 0x3D) на голове робота Ainex.
- `display_node` поддерживает режимы отображения:
  - `emotion`: рисует эмоцию, загружая встроенные (vector) или пользовательские PNG из `resources/emotions/`.
  - `oscillogram`: отображает осциллограмму по данным `/mouth/audio_wave` (128 точек в диапазоне [-1, 1]).
- `display_node` принимает состояния тела:
  - при `/robot/posture` = `fall_forward|fall_backward|fall_left|fall_right` принудительно показывает эмоцию падения `fall_emotion` и отключает осциллограмму до `stand`.
  - при длительном бездействии (тишина + отсутствие ходьбы) показывает `idle_sleep_emotion` (если включено `idle_sleep_enabled`).
- `display_node` поддерживает авто-переключение `auto_mode`:
  - при RMS >= 0.02 переключается на `oscillogram`,
  - по тишине дольше `silence_return_sec` возвращается в `emotion`.
- `audio_capture_node` захватывает ВСЕ звуки системы через PulseAudio, разворачиваемый внутри Docker-контейнера.
- `audio_capture_node` публикует:
  - `/mouth/audio_wave` (`std_msgs/Float32MultiArray`, 128 точек),
  - `/audio/level` (`std_msgs/Float32`, RMS уровень 0..1).
- Пакет сохраняет совместимость интеграции:
  - не меняет внешний контракт топиков (имена и типы сообщений),
  - интеграция выполняется через launch include в `ainex_bringup/launch/bringup.launch`.

### Технические требования
- Топики должны сохраняться без изменений:
  - вход: `/mouth/mode` (`String`), `/mouth/emotion` (`String`), `/mouth/audio_wave` (`Float32MultiArray`), `/robot/posture` (`String`), `/robot/is_moving` (`Bool`),
  - выход: `/mouth/current_mode` (latch, `String`), `/mouth/current_emotion` (latch, `String`), `/audio/level` (`Float32`).
- Настройки конфигурации считываются из `config/sound_mouth_sync.yaml` (через `scripts/sms_config.py`) и переопределяются rosparam/launch args.
- Отображение учитывает параметр `rotate` (для физически перевёрнутого монтажа OLED).

### Производственные требования
- Экосистема: ROS Noetic + Python ноды.
- Зависимости (минимально): `luma.oled`, `Pillow`, `numpy`, `PyYAML`, `rospy`, `rospkg`.
- Аудио: PulseAudio внутри Docker + TCP доступ для хоста через `module-native-protocol-tcp` на порту `4713`.

### Ограничения (Constraints)
- Запрещено “вшивать” в код предположения о конкретных путях аудио-географии: `audio_capture_node` захватывает весь звук через PulseAudio.
- Запрещено трогать `ainex_bringup`: интеграция только через include.
- Пользовательские эмоции:
  - соответствие имени файла имени эмоции в ROS-топике,
  - поддержка изображений 128x64, монохром, 1-bit; загрузка при старте ноды.
- Если пользователь не предоставил `angry.png`/`sleepy.png`, применяются встроенные (vector/анимация) варианты.

## ⚠️ Важные заметки
- Порог “тишины/звука” в авто-режиме: RMS >= 0.02 (см. документацию проекта).
- `idle_require_movement_signal` по умолчанию `true`, чтобы избежать ложного “сна” при отсутствии `joystick_control`.
- Ручная валидация idle-sleep (2026-03-31): при `idle_sleep_enabled=true`, `idle_sleep_sec=120.0`, `idle_require_movement_signal=true`, режиме `emotion`, `audio_level=0.0` и одном сообщении `/robot/is_moving=false` узел `display_node` переключает `/mouth/current_emotion` на `sleepy` и оставляет `/mouth/current_mode="emotion"`.
- Разделение двух OLED подтверждено по коду:
  - 0x3C: `ainex_bringup/scripts/oled_display.py` (системная информация, отдельный service).
  - 0x3D: `sound_mouth_sync/scripts/display_node.py` (рот: эмоции+осциллограмма).
  Они должны работать независимо; падение 0x3D не должно блокировать 0x3C.
- Опционально при двух SSD1306 на одном адресе: env `AINEX_STATS_PAUSE_ON_3C_UNLESS_3D` в `oled_display` (не трогает `bringup.launch`); в YAML `display` — `mouth_display_redraw_after_sec`, `reassert_effective_topics_after_sec`. Независимые картинки на двух экранах требуют разных I2C-адресов (рот 0x3D — перемычка на модуле).
- **Повторяемость симптома «SSID/IP на дисплее рта (ожид. 0x3D)»:** это **не** устранено «навсегда» только обновлением ПО. Если снова **оба** модуля на **0x3C** или с шины пропал **0x3D**, картинка может вернуться. **Длительное выключение питания само по себе** не объясняет дубль — корень в **I2C-адресации и обвязке**, а не в «простое». Рабочая конфигурация: `i2cdetect` показывает **3c и 3d**, рот на **0x3D**. Что делать при повторении — единый чеклист: `README.md` (раздел про два OLED), `doc/SECOND_DISPLAY_ARCHITECTURE.md` §8–8.6, `doc/ARCHITECTURE.md`, `doc/AI_CONTEXT.md`.

## 🧭 Архитектурный план (эмоции в отдельной ноде)
- Цель: вынести логику выбора эмоций (user/fall/idle) из `display_node` в `emotion_node`, не ломая осциллограмму.
- Новая ответственность:
  - `emotion_node`: подписки `/mouth/emotion`, `/robot/posture`, `/robot/is_moving`, `/mouth/audio_wave`, `/audio/level`, `/mouth/mode`; публикует итоговую эмоцию и режим.
  - `display_node`: только рендер OLED (получает итоговый `mode` + `emotion` + waveform).
  - `audio_capture_node`: без изменений.
- Backward compatibility:
  - сохранить входные API `/mouth/mode`, `/mouth/emotion`.
  - сохранить выходные `/mouth/current_mode`, `/mouth/current_emotion`, `/audio/level`.

## ✅ Валидация после переноса в emotion_node
- Пересборка: `catkin build sound_mouth_sync ainex_bringup` — успешно.
- Запуск `ainex_bringup/launch/bringup.launch` из `src` — успешен по основным нодам рта:
  - `/mouth_emotion_node`
  - `/mouth_display_node`
  - `/mouth_audio_capture_node`
- Совместимость команд сохранена:
  - публикация в `/mouth/emotion` меняет `/mouth/effective_emotion` и `/mouth/current_emotion`.
  - публикация в `/mouth/mode` меняет `/mouth/effective_mode` и переключает `/mouth/current_mode`.
- Осциллограмма не сломана:
  - во время `paplay` observed `/mouth/current_mode: emotion -> oscillogram -> emotion`,
  - после звука `/audio/level` возвращается к `0.0`.

## ✅ Валидация 2026-04-01 (пересборка + рантайм на стенде)
- `catkin build sound_mouth_sync ainex_bringup` в `/home/ubuntu/ros_ws` — успешно (bash + `source /opt/ros/noetic/setup.bash`).
- I2C: `i2cdetect -y 1` — на шине **0x3C** и **0x3D** одновременно; `rosparam get /mouth_display_node/i2c_address` → **61** (0x3D для рта).
- Уже работающий `bringup.launch` (localhost): ноды `mouth_emotion_node`, `mouth_display_node`, `mouth_audio_capture_node` в `rosnode list`; `/mouth/audio_wave` публикуется.
- Осциллограмма: при `paplay` (WAV через PulseAudio/USB) в логе `rostopic echo /mouth/current_mode` зафиксирован переход **emotion → oscillogram**.
- Эмоции: `rostopic pub` на `/mouth/mode`=`emotion`, `/mouth/emotion`=`happy` → `/mouth/current_emotion` и `/mouth/effective_emotion` = **happy**, `/mouth/current_mode` = **emotion**.
- Прикладных `srv` у пакета нет; только стандартные `get_loggers` / `set_logger_level` у нод.

## ✅ История изменений (Git-like)
| Версия | Дата | Этап | Изменения | Статус |
|--------|------|------|-----------|--------|
| v0.1.0 | 2026-03-31 | Концепция | Создан минимальный контракт по прочитанным архитектурным документам | Активно |
| v0.1.1 | 2026-03-31 | Детализация | Добавлена `emotion_node` (effective mode/emotion), display_node переведён на `effective_*` топики; сохранены внешние API `/mouth/mode` и `/mouth/emotion`; добавлены compatibility args в `sound_mouth_sync.launch` для include из `ainex_bringup` | Активно |

## 📊 Зависимости и граф компонентов
- `audio_capture_node` -> `/mouth/audio_wave`, `/audio/level` -> `display_node`.
- `display_node` -> I2C -> OLED.
- `display_node` -> читает `/mouth/mode`, `/mouth/emotion`, `/robot/posture`, `/robot/is_moving`.

## 🔌 Интеграция и автозапуск (`ainex_bringup`)
- Автозапуск в systemd: `ainex_bringup/service/start_app_node.service` вызывает `roslaunch ainex_bringup bringup.launch` (через `scripts/source_env.bash`).
- Порядок включения в `ainex_bringup/launch/bringup.launch` (по очереди обработки `<include>`):
  1. Камера: `ainex_peripherals/launch/usb_cam_with_calib.launch` (внутри стартует `usb_cam.launch` и `image_calib.launch`).
  2. Base: `ainex_bringup/launch/base.launch` (стартует `ainex_controller.launch` + `ainex_peripherals/launch/imu.launch`).
  3. Управление джойстиком: `ainex_peripherals/launch/joystick_control.launch` (узлы `joy` и `joystick_control.py`).
  4. Mouth OLED + захват аудио: `sound_mouth_sync/launch/sound_mouth_sync.launch` (стартуют `mouth_display_node` и `mouth_audio_capture_node`, загружается `config/sound_mouth_sync.yaml`).
  5. `web_video_server` (node).
  6. Web/ROS bridge: `ainex_app/launch/rosbridge.launch` (узел `rosbridge_websocket` + `rosapi`).
  7. App gameplay: `ainex_app/launch/start.launch` (включает recognition-ноды и `ainex_app/app_node.py`).
  8. VR телеоперация: `teleop_fetch/launch/teleop_fetch.launch`.
  9. Сервис проверки топиков: `proverka_nod/launch/topici_list.launch`.

- Совместимость include: в `sound_mouth_sync.launch` объявлены compat-аргументы `wait_after_controller`, `wait_controller_timeout_sec`, `controller_settle_sec`, `proceed_without_standup` (см. файл launch).

