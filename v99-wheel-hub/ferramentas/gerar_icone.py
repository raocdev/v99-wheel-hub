"""Gera o ícone do V99 Wheel Hub (assets/icone.ico e icone.png): volante sobre ladrilho azul do aro."""

from pathlib import Path

from PIL import Image, ImageDraw

PASTA = Path(__file__).resolve().parent.parent / "assets"
AZUL = (42, 99, 222, 255)          # azul aro
AZUL_CLARO = (111, 160, 245, 255)  # azul claro (brilho do topo)
LADO = 512

base = Image.new("RGBA", (LADO, LADO), (0, 0, 0, 0))
ImageDraw.Draw(base).rounded_rectangle([0, 0, LADO - 1, LADO - 1], radius=LADO // 5, fill=AZUL)
brilho = Image.new("L", (LADO, LADO), 0)
ImageDraw.Draw(brilho).ellipse([-LADO * 0.3, -LADO * 0.75, LADO * 1.3, LADO * 0.45], fill=90)
mascara = Image.new("L", (LADO, LADO), 0)
ImageDraw.Draw(mascara).rounded_rectangle([0, 0, LADO - 1, LADO - 1], radius=LADO // 5, fill=255)
base.paste(Image.new("RGBA", (LADO, LADO), AZUL_CLARO), (0, 0),
           Image.composite(brilho, Image.new("L", (LADO, LADO), 0), mascara))
volante = Image.open(PASTA / "volante.png").resize((int(LADO * 0.86),) * 2, Image.LANCZOS)
# No ícone o volante fica branco: o cinza claro da ilustração (~165) vira 255 e
# os detalhes escuros continuam escuros.
cinza = volante.convert("L").point(lambda v: min(255, round(v * 255 / 165)))
volante = Image.merge("RGBA", (cinza, cinza, cinza, volante.getchannel("A")))
pos = (LADO - volante.width) // 2
base.alpha_composite(volante, (pos, pos))
base.save(PASTA / "icone.png")
base.save(PASTA / "icone.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("ícone gerado")
