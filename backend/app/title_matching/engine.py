from typing import Optional

from app.title_matching.normalizer import normalize_title
from app.title_matching.candidate_generator import CandidateGenerator
from app.title_matching.decision_engine import score_and_decide
from app.title_matching.types import TitleMatchResult


class TitleMatchEngine:
    def __init__(self, candidate_gen: CandidateGenerator, aliases: dict[str, int]) -> None:
        self._gen = candidate_gen
        self._aliases = aliases
        self._id_to_row = candidate_gen._id_to_row

    def match(
        self,
        title: str,
        show_date: Optional[str] = None,
        theater: Optional[str] = None,
        ticketing_url: Optional[str] = None,
    ) -> TitleMatchResult:
        normalized = normalize_title(title)
        candidates = self._gen.generate(normalized, self._aliases)

        if not candidates:
            return TitleMatchResult(
                suggested_movie_id=0,
                suggested_movie_title="Unknown",
                canonical_movie_id=0,
                confidence=0.0,
                decision="REVIEW",
                reasoning=f"No candidates found for '{title}'. Manual review required.",
                evidence={"fuzzy_top": [], "date_window": "NONE"},
                fired_ai=False,
            )

        result = score_and_decide(
            normalized,
            candidates,
            show_date,
            theater,
            id_to_row=self._id_to_row,
        )

        # T1 ticketing URL poster fetch (best-effort)
        if ticketing_url:
            poster_url = _fetch_og_image(ticketing_url)
            if poster_url:
                result.ticketing_poster_url = poster_url

        # Attach cover_image from winner
        winner_row = self._id_to_row.get(result.suggested_movie_id)
        if winner_row:
            img = winner_row.get('cover_image')
            if img and 'noimage' not in (img or '').lower():
                result.cover_image = img

        return result


def _fetch_og_image(url: str) -> Optional[str]:
    try:
        import httpx
        from html.parser import HTMLParser

        class OGParser(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.og_image: Optional[str] = None

            def handle_starttag(self, tag: str, attrs: list) -> None:
                if tag == 'meta':
                    d = dict(attrs)
                    if d.get('property') == 'og:image' and 'content' in d:
                        self.og_image = d['content']

        resp = httpx.get(
            url,
            timeout=5,
            follow_redirects=True,
            headers={'User-Agent': 'Mozilla/5.0'},
        )
        if resp.status_code == 200:
            parser = OGParser()
            parser.feed(resp.text[:50000])   # only parse first 50kb
            return parser.og_image
    except Exception:
        pass
    return None
