#!/usr/bin/env bash
# Проверка графа после bringup (sound_mouth_sync) и опционально motik.
# На роботе (roscore уже запущен):
#   source ~/ros_ws/devel/setup.bash   # или install/setup.bash
#   bash "$(rospack find sound_mouth_sync)/scripts/verify_bringup_sound_motik.sh"
# При установке в install-space: rosrun sound_mouth_sync verify_bringup_sound_motik.sh

set -u
ok=0
warn=0

die() { echo "ERROR: $*" >&2; exit 1; }

if ! command -v rostopic >/dev/null 2>&1; then
  die "Нет rostopic в PATH — выполните source devel/setup.bash"
fi

echo "=== rosnode list ==="
rosnode list || die "roscore не запущен?"

check_node() {
  local n="$1"
  if rosnode list 2>/dev/null | grep -qx "/${n}"; then
    echo "OK  node /${n}"
    ok=$((ok + 1))
  else
    echo "MISSING node /${n} (ожидается после ainex_bringup + sound_mouth_sync)"
    warn=$((warn + 1))
  fi
}

check_topic() {
  local t="$1"
  if rostopic list 2>/dev/null | grep -qx "${t}"; then
    echo "OK  topic ${t}"
    ok=$((ok + 1))
  else
    echo "MISSING topic ${t}"
    warn=$((warn + 1))
  fi
}

echo ""
echo "=== Ноды sound_mouth_sync (bringup) ==="
check_node mouth_display_node
check_node mouth_audio_capture_node

echo ""
echo "=== Топики sound_mouth_sync ==="
check_topic /mouth/mode
check_topic /mouth/emotion
check_topic /mouth/audio_wave
check_topic /audio/level
check_topic /mouth/current_mode
check_topic /mouth/current_emotion
check_topic /oled_3d/active_driver

echo ""
echo "=== Сообщение владельца OLED (/oled_3d/active_driver, latch) ==="
if timeout 4 rostopic echo -n 1 /oled_3d/active_driver 2>/dev/null; then
  :
else
  timeout 4 rostopic echo /oled_3d/active_driver 2>/dev/null | head -8 || echo "(нет сообщения — проверьте mouth_display_node)"
fi

echo ""
echo "=== motik (если запущен вручную) ==="
if rosnode list 2>/dev/null | grep -qx "/motik_node"; then
  echo "OK  /motik_node активен"
  for t in /emotions /display_mode /oscillogram_display; do
    if rostopic list 2>/dev/null | grep -qx "${t}"; then
      echo "    topic ${t}:"
      rostopic info "${t}" 2>/dev/null | sed -n '1,12p' || true
    fi
  done
else
  echo "    /motik_node не запущен — нормально для штатного bringup"
fi

echo ""
echo "=== Прикладные сервисы (не std rospy get_loggers / set_logger_level) ==="
svc=$(rosservice list 2>/dev/null | grep -E 'mouth_|motik|sound_mouth' | grep -Ev 'get_loggers|set_logger_level' || true)
if [[ -n "${svc}" ]]; then
  echo "Найдены:"
  echo "${svc}"
else
  echo "Нет собственных сервисов sound_mouth_sync/motik — ожидаемо (в нодах нет rospy.Service)"
fi

echo ""
echo "=== Итог: OK-счётчики проверок выше; MISSING — смотрите логи нод и roscore ==="
exit 0
