from pathlib import Path

try:
    import cv2
except ImportError:  # dependency is required at runtime; retain schema/normalizer testability
    cv2 = None  # type: ignore[assignment]
import numpy as np

from app.schemas.models import TemplateDefinition

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_ROOT = ROOT / "data" / "corpus" / "templates"


class TemplateRegistry:
    def __init__(self) -> None:
        self._templates: dict[str, TemplateDefinition] = {}

    def load(self) -> None:
        self._templates = {}
        for path in TEMPLATE_ROOT.glob("T*/template.json"):
            definition = TemplateDefinition.model_validate_json(path.read_text())
            self._templates[definition.template_id] = definition

    def get(self, template_id: str) -> TemplateDefinition:
        if not self._templates:
            self.load()
        return self._templates[template_id]

    def all(self) -> list[TemplateDefinition]:
        if not self._templates:
            self.load()
        return list(self._templates.values())

    def match(
        self, image: np.ndarray, hint: str | None = None
    ) -> tuple[TemplateDefinition | None, float]:
        if hint:
            try:
                return self.get(hint), 1.0
            except KeyError:
                return None, 0.0
        if cv2 is None:
            raise RuntimeError(
                "OpenCV is required for template matching; install backend dependencies"
            )
        # ORB feature matching against safe synthetic blank masters. A completed form retains its frame/labels.
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        orb = cv2.ORB_create(1200)
        keys, desc = orb.detectAndCompute(gray, None)
        if desc is None:
            return None, 0.0
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        best: tuple[TemplateDefinition | None, float] = (None, 0.0)
        for definition in self.all():
            master = cv2.imread(str(TEMPLATE_ROOT / definition.template_id / "blank_preview.png"))
            if master is None:
                continue
            mk, md = orb.detectAndCompute(cv2.cvtColor(master, cv2.COLOR_BGR2GRAY), None)
            if md is None:
                continue
            matches = matcher.match(desc, md)
            score = min(1.0, len(matches) / max(18, min(len(keys), len(mk))))
            if score > best[1]:
                best = (definition, score)
        return best


registry = TemplateRegistry()


def canonical_image_path(template_id: str) -> Path:
    return TEMPLATE_ROOT / template_id / "blank_preview.png"


def align_to_template(image: np.ndarray, template_id: str) -> tuple[np.ndarray, float]:
    """Return canonical-sized image. Falls back safely to aspect resize if registration is weak."""
    if cv2 is None:
        raise RuntimeError(
            "OpenCV is required for template alignment; install backend dependencies"
        )
    master = cv2.imread(str(canonical_image_path(template_id)))
    if master is None:
        raise FileNotFoundError(f"master image missing for {template_id}")
    target_h, target_w = master.shape[:2]
    source = cv2.resize(image, (target_w, target_h))
    orb = cv2.ORB_create(1800)
    a_k, a_d = orb.detectAndCompute(cv2.cvtColor(source, cv2.COLOR_BGR2GRAY), None)
    b_k, b_d = orb.detectAndCompute(cv2.cvtColor(master, cv2.COLOR_BGR2GRAY), None)
    if a_d is None or b_d is None:
        return source, 0.45
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(a_d, b_d)
    if len(matches) < 8:
        return source, 0.50
    matches = sorted(matches, key=lambda m: m.distance)[:100]
    p1 = np.float32([a_k[m.queryIdx].pt for m in matches])
    p2 = np.float32([b_k[m.trainIdx].pt for m in matches])
    homography, mask = cv2.findHomography(p1, p2, cv2.RANSAC, 5.0)
    if homography is None or mask is None or int(mask.sum()) < 8:
        return source, 0.55
    warped = cv2.warpPerspective(
        source, homography, (target_w, target_h), borderValue=(255, 255, 255)
    )
    return warped, min(0.98, 0.55 + float(mask.mean()) * 0.4)
