"""Bale has no parse_mode: every text is Markdown (*bold*, _italic_, [t](url); the marks need a space outside them).
Converts the HTML subset the bot uses (<b> <i> <code> <pre> <u> <a href>) to that Markdown. <code>/<pre>/<u> degrade to plain text.
Risky characters are swapped for look-alikes. (Same converter as the shop bot's transport.py.)"""
import re, html

_TAG = re.compile(r"<(/?)(b|strong|i|em|u|s|code|pre|a|blockquote)(?:\s+href=\"([^\"]*)\")?\s*>", re.I)

def md_escape(t):
    t = t.replace("*", "∗").replace("`", "ˋ").replace("[", "［").replace("]", "］")
    return re.sub(r"(?<!\S)_|_(?!\S)", "＿", t)

def strip_html(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s))

def to_md(s, max_len=4096):
    out, pos, stack = [], 0, []
    for m in _TAG.finditer(s):
        out.append(md_escape(html.unescape(s[pos:m.start()])))
        pos = m.end()
        closing, tag, href = m.group(1) == "/", m.group(2).lower(), m.group(3)
        mark = {"b": "*", "strong": "*", "i": "_", "em": "_"}.get(tag)
        if tag == "a":
            if not closing:
                stack.append(("a", len(out), html.unescape(href or "")))
            elif stack and stack[-1][0] == "a":
                _, idx, url = stack.pop()
                inner = "".join(out[idx:]); del out[idx:]
                out.append(f"[{inner}]({url})" if re.match(r"https?://", url) and inner.strip() else inner)
        elif mark:
            if not closing:
                stack.append((mark, len(out), None)); out.append("\x02" + mark)
            elif stack and stack[-1][0] == mark:
                _, idx, _u = stack.pop()
                inner = "".join(out[idx + 1:])
                if inner.strip(): out.append(mark + "\x01")
                else:
                    del out[idx:]; out.append(inner)
    out.append(md_escape(html.unescape(s[pos:])))
    res = "".join(out)
    res = re.sub(r"(^|\s)\x02", r"\1", res).replace("\x02", " ")
    res = re.sub(r"\x01(?=\s|\Z)", "", res).replace("\x01", " ")
    return res if len(res) <= max_len else res[: max_len - 1] + "…"

def plain_to_md(s, max_len=4096):
    """Text sent with html=False: no tags, but Bale still parses Markdown -> neutralise it."""
    s = md_escape(s)
    return s if len(s) <= max_len else s[: max_len - 1] + "…"
