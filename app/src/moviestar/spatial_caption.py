"""Word states for caption elements on the canonical result clock.

DOM captions retain literal whitespace and the element's layout/typography.
Color-only word changes never introduce a second layout or timing system.
"""
import re
from moviestar.captions import caption_text_matches_tokens
from moviestar.timecodes import parse_timecode


def highlight_state(record, at, *, force=False):
    if not force and (record.get('highlight') or {}).get('mode') != 'spoken-word':
        return {}
    text = record['text']; tokens = record.get('tokens')
    words = list(re.finditer(r'\S+', text))
    if (not tokens or len(tokens) != len(words)
            or not caption_text_matches_tokens(text, tokens)):
        raise ValueError('Regenerate captions with word timing or correct the cue text and tokens together.')
    start, end = parse_timecode(record['from']), parse_timecode(record['to'])
    timed = sorted(enumerate(tokens), key=lambda item: parse_timecode(item[1]['from']))
    cursor, active, windows = start, None, []
    for index, token in timed:
        left, right = parse_timecode(token['from']), parse_timecode(token['to'])
        if right <= left:
            continue  # Zero-duration words remain visible but never light up.
        left, right = max(start, left, cursor), min(end, right)
        if right > left:
            windows.append({'word_index': index, 'from_s': left, 'to_s': right})
            if left <= at < right:
                active = index
        cursor = max(cursor, right)
    return {'active_word': active, 'highlight_color': (record.get('highlight') or {}).get('color') or '#ffe94a',
        'word_spans': [{'start': word.start(), 'end': word.end()} for word in words],
        'word_windows': windows}
