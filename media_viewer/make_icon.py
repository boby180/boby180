"""Draws the program icon (media_viewer.ico) for the EXE."""
from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((8, 8, S - 8, S - 8), radius=48, fill=(23, 26, 31, 255), outline=(242, 182, 61, 255), width=10)
d.rectangle((48, 60, 208, 196), outline=(232, 230, 225, 255), width=10)          # picture frame
d.polygon([(104, 92), (104, 164), (164, 128)], fill=(242, 182, 61, 255))         # play
img.save("media_viewer.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
