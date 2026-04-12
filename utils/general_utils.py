from PIL import ImageOps


class SquarePad:
    """Pad PIL image to square shape while keeping content centered."""

    def __call__(self, image):
        width, height = image.size
        max_side = max(width, height)
        pad_w = max_side - width
        pad_h = max_side - height

        left = pad_w // 2
        right = pad_w - left
        top = pad_h // 2
        bottom = pad_h - top

        return ImageOps.expand(image, border=(left, top, right, bottom), fill=0)

