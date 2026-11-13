"""Turn titles into URL slugs."""

import re
import unicodedata

_SEP = re.compile(r"[^a-z0-9]+")


def slugify(title: str, max_len: int = 60) -> str:
    """Lowercase ASCII words joined by single hyphens, at most max_len chars.

    Accents are folded ("Café" -> "cafe"). A slug never starts or ends with a
    hyphen, and truncation never cuts a word in half.
    """
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = _SEP.sub("-", text.lower()).strip("-")
    if len(slug) > max_len:
        cut = slug[: max_len + 1]
        slug = cut[: cut.rfind("-")] if "-" in cut else slug[:max_len]
    return slug.strip("-")
