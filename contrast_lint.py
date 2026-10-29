#!/usr/bin/env python3
"""
Static contrast linter for the KRMP website.

For every text element on every page it works out:
  * the FINAL text colour (cascade, specificity, !important, inheritance, var(), rgba, opacity)
  * the background actually behind it (nearest opaque ancestor background, blending semi-transparent
    layers, taking every colour stop of a gradient as a worst case)
and reports anything below WCAG AA (4.5:1 normal text, 3:1 large text).

Known limits (it is a linter, not a browser): ignores :hover/:focus states, @media overrides,
::before/::after art, text over photographs (none on this site) and states toggled by JavaScript
other than the floating header, which is evaluated in both its "top of page" and "scrolled" states.
"""
import re, sys, glob, os, hashlib, json
from collections import defaultdict
from bs4 import BeautifulSoup, NavigableString, Comment
import soupsieve as sv

# ------------------------------------------------------------------ colours
NAMED = {'white': (255, 255, 255, 1.0), 'black': (0, 0, 0, 1.0), 'transparent': (0, 0, 0, 0.0)}

def parse_color(s):
    if s is None: return None
    s = s.strip().lower()
    if s in NAMED: return NAMED[s]
    m = re.fullmatch(r'#([0-9a-f]{3,8})', s)
    if m:
        h = m.group(1)
        if len(h) in (3, 4): h = ''.join(c * 2 for c in h)
        if len(h) == 6: return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
        if len(h) == 8: return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16) / 255)
        return None
    m = re.fullmatch(r'rgba?\(([^)]*)\)', s)
    if m:
        parts = [p for p in re.split(r'[,\s/]+', m.group(1).strip()) if p]
        try:
            vals = [float(p.rstrip('%')) * (2.55 if p.endswith('%') else 1) for p in parts[:3]]
            a = float(parts[3].rstrip('%')) / (100 if parts[3].endswith('%') else 1) if len(parts) > 3 else 1.0
            return (vals[0], vals[1], vals[2], a)
        except ValueError:
            return None
    return None

def over(fg, bg):
    """fg (r,g,b,a) painted over an OPAQUE bg -> opaque colour"""
    a = fg[3]
    return (fg[0] * a + bg[0] * (1 - a), fg[1] * a + bg[1] * (1 - a), fg[2] * a + bg[2] * (1 - a), 1.0)

def lum(c):
    def f(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])

def ratio(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)

def hexs(c): return '#%02X%02X%02X' % tuple(int(round(max(0, min(255, v)))) for v in c[:3])

# ------------------------------------------------------------------ CSS parsing
def strip_comments(css): return re.sub(r'/\*.*?\*/', '', css, flags=re.S)

def split_top(text, sep):
    """split on sep at paren/quote depth 0"""
    out, depth, quote, cur = [], 0, None, []
    for ch in text:
        if quote:
            cur.append(ch)
            if ch == quote: quote = None
            continue
        if ch in '"\'': quote = ch; cur.append(ch); continue
        if ch == '(': depth += 1
        elif ch == ')': depth -= 1
        if ch == sep and depth == 0:
            out.append(''.join(cur)); cur = []
        else:
            cur.append(ch)
    out.append(''.join(cur))
    return out

def parse_decls(body):
    decls = []
    for part in split_top(body, ';'):
        if ':' not in part: continue
        prop, val = part.split(':', 1)
        prop, val = prop.strip().lower(), val.strip()
        imp = bool(re.search(r'!\s*important\s*$', val, re.I))
        val = re.sub(r'!\s*important\s*$', '', val, flags=re.I).strip()
        if prop and val: decls.append((prop, val, imp))
    return decls

def parse_css(css):
    css = strip_comments(css)
    rules, i, n = [], 0, len(css)
    while i < n:
        j = css.find('{', i)
        if j < 0: break
        head = css[i:j].strip()
        depth, k, quote = 1, j + 1, None
        while k < n and depth:
            ch = css[k]
            if quote:
                if ch == quote: quote = None
            elif ch in '"\'': quote = ch
            elif ch == '{': depth += 1
            elif ch == '}': depth -= 1
            k += 1
        body = css[j + 1:k - 1]
        i = k
        if head.startswith('@'):      # @media / @keyframes / @font-face ... ignored
            continue
        rules.append((head, parse_decls(body)))
    return rules

def specificity(sel):
    s = re.sub(r'\[[^\]]*\]', ' [a] ', sel)
    ids = len(re.findall(r'#[\w-]+', s))
    cls = len(re.findall(r'\.[\w-]+', s)) + len(re.findall(r'\[a\]', s)) + len(re.findall(r':(?!not)[\w-]+', re.sub(r'::[\w-]+', '', s)))
    tags = len(re.findall(r'(?:^|[\s>+~(,])([a-zA-Z][\w-]*)', re.sub(r'\[[^\]]*\]|[.#:][\w-]+', ' ', s)))
    return ids * 10000 + cls * 100 + tags

STATE_PSEUDO = re.compile(r':(hover|focus|focus-visible|focus-within|active|visited|target|disabled|checked)\b')

# ------------------------------------------------------------------ per-page analysis
class Page:
    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        with open(path, encoding='utf-8') as fh:
            html = fh.read()
        self.html = html
        self.soup = BeautifulSoup(html, 'lxml')
        css = '\n'.join(s.get_text() for s in self.soup.find_all('style'))
        self.rules = parse_css(css)
        self.vars = {}
        self.matched = defaultdict(list)       # id(el) -> [(imp, spec, order, prop, value)]
        self.cache = {}
        self.header_mode = None                # None | 'top' | 'scrolled'
        self.header_under = None
        self._apply_rules(html)

    # custom properties
    def resolve(self, value):
        for _ in range(6):
            new = re.sub(r'var\(\s*(--[\w-]+)\s*(?:,\s*([^)]*))?\)',
                         lambda m: self.vars.get(m.group(1), (m.group(2) or '').strip()), value)
            if new == value: break
            value = new
        return value

    def _apply_rules(self, html):
        for head, decls in self.rules:
            if head.strip() == ':root':
                for prop, val, _ in decls:
                    if prop.startswith('--'): self.vars[prop] = val
        order = 0
        for head, decls in self.rules:
            for sel in split_top(head, ','):
                sel = sel.strip()
                if not sel or '::' in sel or STATE_PSEUDO.search(sel) or sel == ':root': continue
                # cheap pre-filter: skip rules whose classes / ids never occur on this page
                needed = re.findall(r'[.#]([\w-]+)', re.sub(r'\[[^\]]*\]|:not\([^)]*\)', '', sel))
                if any(nm not in html for nm in needed): continue
                try:
                    els = self.soup.select(sel)
                except Exception:
                    continue
                spec = specificity(sel)
                for el in els:
                    for prop, val, imp in decls:
                        self.matched[id(el)].append((imp, spec, order, prop, val))
                order += 1
        for el in self.soup.find_all(True):
            st = el.get('style')
            if st:
                for prop, val, imp in parse_decls(st):
                    self.matched[id(el)].append((imp, 100000, 10 ** 9, prop, val))

    # ---- computed properties
    def declared(self, el):
        decls = sorted(self.matched.get(id(el), []), key=lambda t: (t[0], t[1], t[2]))
        out = {}
        bg = None            # list of colours (worst-case set) or None = transparent
        for imp, spec, order, prop, val in decls:
            v = self.resolve(val)
            if prop == 'background':
                bg = self._bg_colors(v)
            elif prop == 'background-color':
                c = parse_color(v)
                bg = [c] if c else bg
            elif prop == 'background-image':
                if 'gradient' in v:
                    bg = self._bg_colors(v)
            else:
                out[prop] = v
        out['_bg'] = bg
        return out

    def _bg_colors(self, v):
        if 'gradient' in v:
            toks = re.findall(r'#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|\b(?:white|black|transparent)\b', v)
            cols = [parse_color(t) for t in toks]
            cols = [c for c in cols if c]
            return cols or None
        if re.search(r'\burl\(', v) and not re.search(r'#[0-9a-fA-F]{3,8}\b|rgba?\(|\b(white|black)\b', re.sub(r'url\([^)]*\)', '', v)):
            return None
        toks = re.findall(r'#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|\b(?:white|black|transparent)\b', re.sub(r'url\([^)]*\)', '', v))
        for t in toks:
            c = parse_color(t)
            if c: return [c]
        return None

    def computed(self, el):
        key = id(el)
        if key in self.cache: return self.cache[key]
        parent = el.parent if el.parent is not None and el.parent.name not in (None, '[document]') else None
        pc = self.computed(parent) if parent is not None else {'color': (16, 16, 16, 1.0), 'size': 16.0, 'bold': False}
        d = self.declared(el)
        res = dict(pc)
        res.pop('display_none', None)
        # colour
        cv = d.get('color')
        if cv and cv.lower() not in ('inherit', 'currentcolor'):
            c = parse_color(cv)
            if c: res['color'] = c
        # font size
        fs = d.get('font-size')
        size = pc['size']
        if fs:
            m = re.search(r'(-?[\d.]+)\s*(px|rem|em|%)?', re.sub(r'^clamp\(', '', fs))
            if m:
                v = float(m.group(1)); u = m.group(2) or 'px'
                size = v if u == 'px' else v * 16 if u == 'rem' else v * pc['size'] if u == 'em' else pc['size'] * v / 100
        res['size'] = size
        fw = d.get('font-weight')
        if fw:
            res['bold'] = fw in ('bold', 'bolder') or (fw.isdigit() and int(fw) >= 600)
        elif el.name in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'b', 'strong', 'th'):
            res['bold'] = True
        # opacity (multiplies down the tree; opacity:0 is treated as an animation start state)
        op = 1.0
        if 'opacity' in d:
            try: op = float(d['opacity'])
            except ValueError: op = 1.0
            if op < 0.05: op = 1.0
        res['opacity'] = pc.get('opacity', 1.0) * op
        res['display_none'] = pc.get('display_none', False) or d.get('display', '').strip() == 'none' or el.has_attr('hidden')
        res['own_bg'] = d['_bg']
        self.cache[key] = res
        return res

    # ---- background behind an element
    def bg_candidates(self, el, _depth=0):
        if el is None or el.name in (None, '[document]'):
            return [(255, 255, 255, 1.0)]
        comp = self.computed(el)
        own = comp.get('own_bg')
        parent = el.parent
        if el.name == 'header' and 'site-header' in (el.get('class') or []) and self.header_under is not None:
            under = self.header_under
        else:
            under = None
        if not own or all(c[3] == 0 for c in own):
            return under if under is not None else self.bg_candidates(parent, _depth + 1)
        beneath = under if under is not None else None
        out = []
        for c in own:
            if c[3] >= 0.999:
                out.append(c)
            else:
                if beneath is None: beneath = self.bg_candidates(parent, _depth + 1)
                for b in beneath: out.append(over(c, b))
        return out[:8]

    # ---- find text elements
    def text_elements(self):
        body = self.soup.body
        for el in body.find_all(True):
            if el.name in ('script', 'style', 'noscript', 'svg', 'path', 'title', 'option', 'textarea'): continue
            if any(p.name == 'svg' for p in el.parents): continue
            txt = ''.join(str(ch) for ch in el.children if isinstance(ch, NavigableString) and not isinstance(ch, Comment)).strip()
            if not txt: continue
            yield el, txt

def describe(el):
    cls = '.'.join((el.get('class') or [])[:2])
    me = el.name + ('.' + cls if cls else '')
    ctx = ''
    for p in el.parents:
        if p.name == 'section':
            th = p.get('data-theme'); pc = '.'.join((p.get('class') or [])[:2])
            ctx = 'section' + (f'[{th}]' if th else '') + (('.' + pc) if pc else '')
            break
        if p.name in ('footer', 'header', 'nav'):
            ctx = p.name; break
    return f'{ctx} > {me}' if ctx else me

def analyse(path, states_for_header=True):
    """Mirrors the site's header script: at the top of the page the header is light only when the
    body lacks data-start-dark; once scrolled it is light over light sections and dark over dark ones."""
    page = Page(path)
    fails = []
    body = page.soup.body
    sections = body.find_all('section')
    first_section = sections[0] if sections else None
    white = (255, 255, 255, 1.0)
    first_bg = page.bg_candidates(first_section) if first_section is not None else [white]
    light_bgs, dark_bgs = [white], []
    for s in sections:
        bgs = page.bg_candidates(s)
        (dark_bgs if max(lum(c) for c in bgs) < 0.25 else light_bgs).extend(bgs)

    header = body.find('header', class_='site-header')
    states = [None]
    if header is not None and states_for_header:
        states = ['top', 'scrolled-light'] + (['scrolled-dark'] if dark_bgs else [])

    for state in states:
        page.cache.clear()
        if header is not None and state is not None:
            cls = set(header.get('class') or []) - {'is-light', 'is-scrolled', 'is-dark-bg'}
            if state == 'top':
                if max(lum(x) for x in first_bg) >= 0.25: cls.add('is-light')   # script: light unless a dark section is under the header
                page.header_under = first_bg
            elif state == 'scrolled-light':
                cls.update({'is-scrolled', 'is-light'}); page.header_under = light_bgs
            else:
                cls.update({'is-scrolled', 'is-dark-bg'}); page.header_under = dark_bgs
            header['class'] = list(cls)
            page.matched = defaultdict(list); page._apply_rules(page.html)
        for el, txt in page.text_elements():
            in_header = header is not None and (el is header or header in el.parents)
            if state not in (None, 'top') and not in_header: continue
            comp = page.computed(el)
            if comp['display_none']: continue
            fg = comp['color']
            size, bold = comp['size'], comp['bold']
            large = size >= 24 or (size >= 18.66 and bold)
            need = 3.0 if large else 4.5
            worst = None
            for bg in page.bg_candidates(el):
                f = over(fg, bg) if fg[3] < 1 else fg
                op = comp['opacity']
                if op < 1: f = over((f[0], f[1], f[2], op), bg)
                r = ratio(f, bg)
                if worst is None or r < worst[0]: worst = (r, bg, f)
            if worst and worst[0] < need:
                fails.append({'page': page.name, 'where': describe(el) + (f' [{state}]' if state and in_header else ''),
                              'fg': hexs(worst[2]), 'bg': hexs(worst[1]), 'ratio': round(worst[0], 2), 'need': need,
                              'size': round(size), 'text': txt[:44]})
    page.header_under = None
    return fails

def main(paths):
    allf = []
    for p in paths:
        allf += analyse(p)
    return allf

if __name__ == '__main__':
    files = sys.argv[1:] or sorted(glob.glob('*.html'))   # run from the website folder, or pass file names
    fails = main(files)
    groups = defaultdict(list)
    for f in fails: groups[(f['where'], f['fg'], f['bg'])].append(f)
    print(f"{len(files)} pages analysed; {len(fails)} failing text elements in {len(groups)} distinct combinations\n")
    for (where, fg, bg), items in sorted(groups.items(), key=lambda kv: (kv[1][0]['ratio'], -len(kv[1]))):
        pages = sorted({i['page'] for i in items})
        print(f"{items[0]['ratio']:>5} (need {items[0]['need']})  text {fg} on {bg}  | {where}  | {len(items)}x on {len(pages)} page(s): {', '.join(pages[:4])}{'…' if len(pages) > 4 else ''}")
        print(f"        e.g. “{items[0]['text']}”  ({items[0]['size']}px)")
    json.dump(fails, open('contrast_report.json', 'w'), indent=1)
