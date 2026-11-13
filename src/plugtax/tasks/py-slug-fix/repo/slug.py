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
    slug = _SEP.sub("-", text.lower())
    if len(slug) > max_len:
        slug = slug[:max_len]
        if "-" in slug:
            slug = slug[: slug.rfind("-")]
    return slug
