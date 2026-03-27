# Custom Emotions — Руководство

## Быстрый старт

1. Создайте PNG 128x64 пикселей, черно-белое (1-bit)
2. Назовите файл именем эмоции: `wink.png`, `yawn.png`, `thinking.png`
3. Положите в `resources/emotions/`
4. Перезапустите ноду
5. Используйте: `rostopic pub -1 /mouth/emotion std_msgs/String "data: 'wink'"`

## Требования к изображению

| Параметр | Значение |
|----------|----------|
| Размер | 128 x 64 пикселей |
| Цветность | 1-bit (черно-белое) |
| Формат | PNG, BMP или GIF (без анимации) |
| Фон | Черный (пиксели = 0) |
| Рисунок | Белый (пиксели = 255 → светятся на OLED) |

Изображения другого размера будут автоматически масштабированы до 128x64 и
конвертированы в 1-bit при загрузке.

## Способы создания

### Из любого PNG (ImageMagick)

```bash
convert input.png -resize 128x64! -monochrome resources/emotions/myemotion.png
```

### Из любого PNG (Python / Pillow)

```python
from PIL import Image
img = Image.open("input.png").resize((128, 64)).convert("1")
img.save("resources/emotions/myemotion.png")
```

### С нуля (Python / Pillow)

```python
from PIL import Image, ImageDraw

img = Image.new("1", (128, 64), 0)  # черный фон
draw = ImageDraw.Draw(img)

# Пример: широкая улыбка
draw.arc((20, 10, 108, 54), 0, 180, fill=255, width=4)

img.save("resources/emotions/big_smile.png")
```

### Из SVG

```bash
# SVG → PNG через Inkscape
inkscape input.svg --export-filename=tmp.png -w 128 -h 64
convert tmp.png -monochrome resources/emotions/myemotion.png
```

## Именование

- Имя файла (без расширения) = имя эмоции в ROS-топике
- Только строчные латинские буквы, цифры, подчёркивания
- Примеры: `wink.png`, `big_smile.png`, `thinking_face.png`
- Пользовательские эмоции имеют приоритет над встроенными (если имена совпадают)

## Анимация `cat`

Два кадра в `resources/emotions/`:

- `cat_frame0.png`
- `cat_frame1.png`

Имя эмоции в топике: `cat`. Кадры чередуются каждые ~0.5 с. Файлы `cat_frame0` / `cat_frame1` не являются отдельными именами эмоций (в топик их не публикуйте).

## Встроенные эмоции

Следующие эмоции рисуются программно и не требуют PNG:

`neutral`, `happy`, `sad`, `angry`, `surprised`, `excited`, `sleepy`, `love`,
`confused`, `scared`, `bored`, `calm`, `disgusted`, `tired`

Плюс **`cat`** — если загружены `cat_frame0.png` и `cat_frame1.png`; иначе при выборе `cat` показывается нейтральное лицо.
