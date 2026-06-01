import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont


@dataclass
class BrandConfig:
    logo_path: Optional[Path] = None
    primary_color: str = "#2E75B6"
    secondary_color: str = "#1A4D8A"
    accent_color: str = "#F0A500"
    heading_font_path: Optional[Path] = None
    body_font_path: Optional[Path] = None
    logo_position: str = "bottom_right"   # bottom_right | bottom_left | top_right | top_left
    logo_scale: float = 0.15              # relative to image width
    padding: int = 40
    # branded-card template (portrait 1080x1350)
    template_style: str = "branded_card"  # "square" | "branded_card"
    niche_box_palette: list[str] = field(default_factory=lambda: [
        "#ffbf00", "#0076cb", "#5e17eb", "#00bf63", "#000000", "#ff751f"])
    niche_box_color: str = "#ff751f"
    niche_box_font_size: int = 40
    description_box_alpha: float = 0.79
    show_logo: bool = True


class PillowBrandEngine:
    INSTAGRAM_SIZES: dict[str, tuple[int, int]] = {
        "square": (1080, 1080),
        "portrait": (1080, 1350),
        "landscape": (1080, 608),
    }

    def __init__(self, config: BrandConfig):
        self.config = config
        self._logo: Optional[Image.Image] = None
        if config.logo_path and Path(config.logo_path).exists():
            self._logo = Image.open(config.logo_path).convert("RGBA")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply_brand(
        self,
        image_bytes: bytes,
        text_overlay: Optional[str] = None,
        subtitle: Optional[str] = None,
        aspect: str = "square",
    ) -> bytes:
        """Apply branding to an existing photo and return JPEG bytes."""
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        target = self.INSTAGRAM_SIZES[aspect]
        img = self._resize_and_crop(img, target)

        if text_overlay:
            img = self._add_dark_overlay(img, opacity=0.4)

        draw = ImageDraw.Draw(img)

        if text_overlay:
            font = self._load_font(self.config.heading_font_path, 64)
            self._draw_centered_text(
                draw, text_overlay, font, self.config.primary_color,
                y_frac=0.35, max_width=target[0] - 120, img_size=target,
            )

        if subtitle:
            font_body = self._load_font(self.config.body_font_path, 36)
            self._draw_centered_text(
                draw, subtitle, font_body, "#FFFFFF",
                y_frac=0.55, max_width=target[0] - 120, img_size=target,
            )

        if self._logo:
            self._add_logo(img)

        return self._to_jpeg(img)

    def create_carousel_slide(
        self,
        slide_number: int,
        total_slides: int,
        heading: str,
        body_text: str,
        background_color: Optional[str] = None,
        background_image: Optional[bytes] = None,
    ) -> bytes:
        """Render a branded carousel slide and return JPEG bytes."""
        target = self.INSTAGRAM_SIZES["square"]

        if background_image:
            img = Image.open(io.BytesIO(background_image)).convert("RGBA")
            img = self._resize_and_crop(img, target)
            img = self._add_dark_overlay(img, opacity=0.5)
        else:
            color = background_color or self.config.primary_color
            img = Image.new("RGBA", target, self._hex_to_rgba(color))

        draw = ImageDraw.Draw(img)

        # Slide counter (e.g. "2/5")
        font_small = self._load_font(self.config.body_font_path, 24)
        draw.text(
            (target[0] - self.config.padding - 60, self.config.padding),
            f"{slide_number}/{total_slides}",
            fill="#FFFFFF",
            font=font_small,
        )

        # Heading
        font_heading = self._load_font(self.config.heading_font_path, 56)
        self._draw_centered_text(
            draw, heading, font_heading, "#FFFFFF",
            y_frac=0.25, max_width=target[0] - 160, img_size=target,
        )

        # Body
        font_body = self._load_font(self.config.body_font_path, 32)
        self._draw_centered_text(
            draw, body_text, font_body, "#E0E0E0",
            y_frac=0.50, max_width=target[0] - 160, img_size=target,
        )

        if self._logo:
            self._add_logo(img)

        return self._to_jpeg(img)

    def create_branded_card(
        self,
        background_image: bytes,
        niche_text: str,
        description_text: str,
        niche_box_color: Optional[str] = None,
        show_logo: Optional[bool] = None,
        page_number: Optional[int] = None,
        total_slides: Optional[int] = None,
    ) -> bytes:
        """Render the portrait 1080x1350 branded card and return JPEG bytes."""
        target = self.INSTAGRAM_SIZES["portrait"]
        tw, th = target
        pad = self.config.padding

        img = Image.open(io.BytesIO(background_image)).convert("RGBA")
        img = self._resize_and_crop(img, target)

        # Resolve overrides
        box_color = niche_box_color or self.config.niche_box_color
        if box_color not in self.config.niche_box_palette:
            box_color = self.config.niche_box_color
        logo_on = self.config.show_logo if show_logo is None else show_logo

        draw = ImageDraw.Draw(img)
        box_left = pad
        box_right = tw - pad
        box_inner_w = box_right - box_left - 2 * pad

        # --- Niche box (solid, bold) sitting in the lower third ---
        niche_font = self._load_font(self.config.heading_font_path, self.config.niche_box_font_size)
        niche_label = (niche_text or "").strip()
        niche_lines = self._wrap_lines(draw, niche_label, niche_font, box_inner_w) if niche_label else []
        niche_lh = draw.textbbox((0, 0), "Ag", font=niche_font)[3] + 8
        niche_h = niche_lh * max(len(niche_lines), 1) + pad
        niche_top = int(th * 0.62)
        if niche_lines:
            draw.rectangle(
                [box_left, niche_top, box_right, niche_top + niche_h],
                fill=self._hex_to_rgba(box_color, 255),
            )
            y = niche_top + pad // 2
            for line in niche_lines:
                draw.text((box_left + pad, y), line, fill="#FFFFFF", font=niche_font)
                y += niche_lh

        # --- Description box (white, semi-transparent) directly below ---
        desc_text = (description_text or "").strip()
        if desc_text:
            desc_font = self._load_font(self.config.body_font_path, 48)
            desc_lines = self._wrap_lines(draw, desc_text, desc_font, box_inner_w)
            desc_lh = draw.textbbox((0, 0), "Ag", font=desc_font)[3] + 10
            desc_top = niche_top + niche_h + 12
            desc_h = desc_lh * len(desc_lines) + pad

            overlay = Image.new("RGBA", target, (0, 0, 0, 0))
            odraw = ImageDraw.Draw(overlay)
            alpha = int(255 * self.config.description_box_alpha)
            odraw.rectangle(
                [box_left, desc_top, box_right, desc_top + desc_h],
                fill=(255, 255, 255, alpha),
            )
            img = Image.alpha_composite(img, overlay)
            draw = ImageDraw.Draw(img)
            y = desc_top + pad // 2
            for line in desc_lines:
                draw.text((box_left + pad, y), line, fill="#000000", font=desc_font)
                y += desc_lh

        # --- Logo (top-right) ---
        if logo_on and self._logo:
            self._add_logo(img, position="top_right")

        # --- Manual page number ---
        if page_number is not None:
            num_font = self._load_font(self.config.body_font_path, 28)
            label = f"{page_number}/{total_slides}" if total_slides else str(page_number)
            draw = ImageDraw.Draw(img)
            draw.text((pad, th - pad - 28), label, fill="#FFFFFF", font=num_font)

        return self._to_jpeg(img)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resize_and_crop(img: Image.Image, target: tuple[int, int]) -> Image.Image:
        tw, th = target
        iw, ih = img.size
        scale = max(tw / iw, th / ih)
        new_w, new_h = int(iw * scale), int(ih * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - tw) // 2
        top = (new_h - th) // 2
        return img.crop((left, top, left + tw, top + th))

    @staticmethod
    def _add_dark_overlay(img: Image.Image, opacity: float = 0.4) -> Image.Image:
        overlay = Image.new("RGBA", img.size, (0, 0, 0, int(255 * opacity)))
        return Image.alpha_composite(img, overlay)

    @staticmethod
    def _wrap_lines(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont,
        max_width: int,
    ) -> list[str]:
        """Word-wrap text to fit max_width pixels."""
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            test = f"{current} {word}".strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    def _draw_centered_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont,
        color: str,
        y_frac: float,
        max_width: int,
        img_size: tuple[int, int],
    ) -> None:
        lines = self._wrap_lines(draw, text, font, max_width)

        line_height = draw.textbbox((0, 0), "Ag", font=font)[3] + 8
        total_height = line_height * len(lines)
        y = img_size[1] * y_frac - total_height / 2

        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            x = (img_size[0] - (bbox[2] - bbox[0])) / 2
            draw.text((x, y), line, fill=color, font=font)
            y += line_height

    def _add_logo(self, img: Image.Image, position: Optional[str] = None) -> None:
        if not self._logo:
            return
        w = img.size[0]
        logo_w = int(w * self.config.logo_scale)
        ratio = logo_w / self._logo.size[0]
        logo_h = int(self._logo.size[1] * ratio)
        logo = self._logo.resize((logo_w, logo_h), Image.LANCZOS)

        p = self.config.padding
        iw, ih = img.size
        pos_map = {
            "bottom_right": (iw - logo_w - p, ih - logo_h - p),
            "bottom_left":  (p, ih - logo_h - p),
            "top_right":    (iw - logo_w - p, p),
            "top_left":     (p, p),
        }
        key = position or self.config.logo_position
        pos = pos_map.get(key, pos_map["bottom_right"])
        img.paste(logo, pos, logo)

    @staticmethod
    def _load_font(font_path: Optional[Path], size: int) -> ImageFont.FreeTypeFont:
        if font_path and Path(font_path).exists():
            return ImageFont.truetype(str(font_path), size=size)
        return ImageFont.load_default(size=size)

    @staticmethod
    def _to_jpeg(img: Image.Image) -> bytes:
        final = img.convert("RGB")
        buf = io.BytesIO()
        final.save(buf, format="JPEG", quality=95)
        return buf.getvalue()

    @staticmethod
    def _hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (r, g, b, alpha)
