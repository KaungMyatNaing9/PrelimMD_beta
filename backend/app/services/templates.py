import shutil
from pathlib import Path

try:
    import cv2
except ImportError:  # dependency is required at runtime; retain schema/normalizer testability
    cv2 = None  # type: ignore[assignment]
import numpy as np

from app.schemas.models import TemplateDefinition

ROOT = Path(__file__).resolve().parents[3]
# Corpus masters are frozen evaluation ground truth; templates derived at runtime are kept
# separate so deriving one can never mutate the benchmark.
TEMPLATE_ROOT = ROOT / "data" / "corpus" / "templates"
DERIVED_ROOT = ROOT / "data" / "templates"
TEMPLATE_ROOTS = (TEMPLATE_ROOT, DERIVED_ROOT)

# Below this many geometrically-consistent matches, a "match" is noise.
MIN_GEOMETRIC_MATCHES = 8
GEOMETRIC_MATCH_POOL = 300
# Measured separation is wide: an unrelated form scores ~0.04, the right template ~1.0.
MATCH_THRESHOLD = 0.5


def _geometric_score(matches, query_keys, master_keys) -> float:
    """Fraction of descriptor matches that agree on a single geometric transform.

    Raw match counts do not discriminate between intake forms: they all share boxes,
    rules and similar type, so unrelated pages score nearly as high as the right one.
    Requiring the matches to be consistent with one homography is what separates a real
    template hit from generic form-shaped resemblance.
    """
    if cv2 is None or len(matches) < MIN_GEOMETRIC_MATCHES:
        return 0.0
    # Keep a wide pool: capping too tightly saturates every candidate at 1.0 and the
    # ranking between similar templates collapses into an arbitrary tie.
    ranked = sorted(matches, key=lambda m: m.distance)[:GEOMETRIC_MATCH_POOL]
    source = np.float32([query_keys[m.queryIdx].pt for m in ranked])
    target = np.float32([master_keys[m.trainIdx].pt for m in ranked])
    _, mask = cv2.findHomography(source, target, cv2.RANSAC, 5.0)
    if mask is None:
        return 0.0
    inliers = int(mask.sum())
    if inliers < MIN_GEOMETRIC_MATCHES:
        return 0.0
    return inliers / len(ranked)


def release(template_id: str) -> bool:
    """Delete a template that was learned at runtime, and re-read the registry.

    Deriving a template *registers* it, and a registered template is matched against every
    later upload -- so a script that learns one per form to inspect it silently changes
    what the extractor reads next. Surveying seven forms once left seventeen behind, and
    two Gate 3 figures were reported against that polluted registry before anyone noticed.
    Anything that derives a template for a look, rather than to keep, has to be able to put
    it back, and a person who learns a bad one should be able to remove it.

    Only the derived range can be deleted. The corpus templates are evidence and are not
    ours to remove at runtime.

    A template whose folder has already gone can still be *live in memory*: the registry
    caches what it loaded, so an entry whose directory was removed without a reload keeps
    being matched against uploads while this refused to delete it -- a phantom template,
    influencing every reading and removable only by restarting the server. Found exactly
    that (a stale T51) during the 2026-09-11 stabilisation pass. Reloading clears it,
    because `load()` rebuilds the map from disk.
    """
    folder = DERIVED_ROOT / template_id
    if not folder.is_dir():
        # A phantom has no folder under *any* root. Testing only the derived root would
        # report a corpus template as released: those live elsewhere on disk and are not
        # ours to remove, which is the whole point of the check above.
        on_disk = any((root / template_id).is_dir() for root in TEMPLATE_ROOTS)
        known = any(t.template_id == template_id for t in registry.all())
        if known and not on_disk:
            registry.load()
            return True
        return False
    shutil.rmtree(folder)
    registry.load()
    return True


class TemplateRegistry:
    def __init__(self) -> None:
        self._templates: dict[str, TemplateDefinition] = {}

    def load(self) -> None:
        self._templates = {}
        for root in TEMPLATE_ROOTS:
            for path in root.glob("T*/template.json"):
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
        """Identify the template a page came from, with its measured confidence.

        A ``hint`` narrows the search to one template; it does **not** assert a match.
        The returned score is always measured against the image, so a caller naming the
        wrong template gets a low score rather than false certainty.
        """
        if cv2 is None:
            raise RuntimeError(
                "OpenCV is required for template matching; install backend dependencies"
            )
        if hint:
            try:
                candidates = [self.get(hint)]
            except KeyError:
                return None, 0.0
        else:
            candidates = self.all()

        # ORB feature matching against safe synthetic blank masters. A completed form retains its frame/labels.
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        orb = cv2.ORB_create(1200)
        keys, desc = orb.detectAndCompute(gray, None)
        if desc is None:
            return None, 0.0
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        best: tuple[TemplateDefinition | None, float] = (None, 0.0)
        for definition in candidates:
            master = cv2.imread(str(canonical_image_path(definition.template_id)))
            if master is None:
                continue
            mk, md = orb.detectAndCompute(cv2.cvtColor(master, cv2.COLOR_BGR2GRAY), None)
            if md is None:
                continue
            score = _geometric_score(matcher.match(desc, md), keys, mk)
            if score > best[1]:
                best = (definition, score)
        return best


registry = TemplateRegistry()


def canonical_image_path(template_id: str) -> Path:
    for root in TEMPLATE_ROOTS:
        candidate = root / template_id / "blank_preview.png"
        if candidate.exists():
            return candidate
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
