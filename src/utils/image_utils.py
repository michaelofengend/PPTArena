import base64
import io
import math
import numpy as np
from PIL import Image

def _pil_from_b64(image_b64: str):
    """Decode base64 to PIL.Image with safety guards."""
    try:
        if not image_b64:
            return None
        raw = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return img
    except Exception:
        return None

def _resize_for_metric(img: Image.Image, max_side: int = 512) -> Image.Image:
    """Downscale while preserving aspect ratio to keep metrics fast and stable."""
    w, h = img.size
    if max(w, h) <= max_side:
        return img
    if w >= h:
        new_w = max_side
        new_h = int(h * (max_side / float(w)))
    else:
        new_h = max_side
        new_w = int(w * (max_side / float(h)))
    return img.resize((new_w, new_h), Image.BILINEAR)

def _to_gray_np(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L"), dtype=np.float32) / 255.0

def _compute_ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Simple SSIM implementation (windowless approximation) for speed."""
    # Avoid heavy deps; use luminance/contrast/structure components approximated globally
    C1 = 0.01 ** 2
    C2 = 0.03 ** 2
    mu_a = float(a.mean())
    mu_b = float(b.mean())
    sigma_a2 = float(a.var())
    sigma_b2 = float(b.var())
    sigma_ab = float(((a - mu_a) * (b - mu_b)).mean())
    num = (2 * mu_a * mu_b + C1) * (2 * sigma_ab + C2)
    den = (mu_a ** 2 + mu_b ** 2 + C1) * (sigma_a2 + sigma_b2 + C2)
    if den == 0:
        return 0.0
    val = num / den
    # Clamp to [0,1]
    return max(0.0, min(1.0, float(val)))

def _dct_8x8_hash(img: Image.Image) -> int:
    """Compute a simple pHash-like 64-bit hash using DCT on a small grayscale image."""
    try:
        # Resize to 32x32, compute 8x8 low-frequency DCT
        size = 32
        gray = _to_gray_np(img.resize((size, size), Image.BILINEAR))
        # 2D DCT (slow but fine for 32x32)
        N = size
        dct = np.zeros((N, N), dtype=np.float32)
        for u in range(N):
            for v in range(N):
                cu = math.sqrt(1 / N) if u == 0 else math.sqrt(2 / N)
                cv = math.sqrt(1 / N) if v == 0 else math.sqrt(2 / N)
                s = 0.0
                for x in range(N):
                    for y in range(N):
                        s += gray[x, y] * math.cos(((2 * x + 1) * u * math.pi) / (2 * N)) * math.cos(((2 * y + 1) * v * math.pi) / (2 * N))
                dct[u, v] = cu * cv * s
        low = dct[:8, :8]
        # Exclude DC coefficient when computing median
        ac = low.flatten()[1:]
        med = float(np.median(ac)) if ac.size else 0.0
        bits = (low > med).astype(np.uint8).flatten()
        # Build 64-bit integer
        h = 0
        for bit in bits[:64]:
            h = (h << 1) | int(bit)
        return int(h)
    except Exception:
        return 0

def _hamming_distance64(a: int, b: int) -> int:
    x = (a ^ b) & ((1 << 64) - 1)
    # Kernighan's popcount
    cnt = 0
    while x:
        x &= x - 1
        cnt += 1
    return cnt

def image_to_base64(image_path):
    """Converts an image file to a base64 encoded string."""
    try:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        print(f"Error converting image {image_path} to base64: {e}")
        return None
