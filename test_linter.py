import os, tempfile, textwrap, unittest
import contrast_lint as L

def run(css, body, body_attrs='', head_extra=''):
    html = f'<!DOCTYPE html><html><head><style>{css}</style>{head_extra}</head><body {body_attrs}>{body}</body></html>'
    with tempfile.NamedTemporaryFile('w', suffix='.html', delete=False, dir='/tmp') as f:
        f.write(html); path = f.name
    try: return L.analyse(path)
    finally: os.unlink(path)

def texts(fails): return sorted(f['text'] for f in fails)

class T(unittest.TestCase):
    def test_basic_pass_and_fail(self):
        f = run(':root{--w:#fff}', '<p style="color:#fff;background:#fff">invisible</p><p style="color:#101010;background:#fff">fine</p>')
        self.assertEqual(texts(f), ['invisible'])
        self.assertEqual(f[0]['ratio'], 1.0)

    def test_known_gold_on_white_bug_is_flagged_and_fix_passes(self):
        f = run('', '<p style="color:#F0A81E">old gold</p><p style="color:#8A5C00">new gold</p>')
        self.assertEqual(texts(f), ['old gold']); self.assertEqual(f[0]['ratio'], 2.03)

    def test_inheritance_from_dark_section(self):
        css = 'section[data-theme="dark"]{background:#0C3D1C;color:#fff} .light{color:#fff}'
        f = run(css, '<section data-theme="dark"><h2>inherits white</h2><p class="x">also white</p></section><section><p class="light">white on white</p></section>')
        self.assertEqual(texts(f), ['white on white'])

    def test_child_override_breaks_on_dark(self):
        css = 'section{background:#0C3D1C;color:#fff} .dim{color:#1b2117}'
        f = run(css, '<section><p>ok</p><p class="dim">dark on dark</p></section>')
        self.assertEqual(texts(f), ['dark on dark'])

    def test_specificity_and_important(self):
        css = '.a{color:#fff} div.a{color:#101010} .b{color:#101010 !important} #i.b{color:#fff}'
        f = run(css, '<div class="a">dark wins on specificity</div><p class="b">important wins</p>')
        self.assertEqual(f, [])
        f = run('.a{color:#101010} div.a{color:#fff}', '<div class="a">white wins</div>')
        self.assertEqual(texts(f), ['white wins'])

    def test_inline_beats_rules_but_not_important(self):
        f = run('p{color:#fff !important}', '<p style="color:#101010">important beats inline</p>')
        self.assertEqual(texts(f), ['important beats inline'])
        f = run('p{color:#fff}', '<p style="color:#101010">inline beats rule</p>')
        self.assertEqual(f, [])

    def test_css_variables(self):
        css = ':root{--ink:#101010;--bg:#fff;--bad:#f5f5f5} .ok{color:var(--ink);background:var(--bg)} .no{color:var(--bad);background:var(--bg)}'
        f = run(css, '<p class="ok">ok</p><p class="no">bad</p><p style="color:var(--missing,#101010)">fallback</p>')
        self.assertEqual(texts(f), ['bad'])

    def test_semi_transparent_background_blends_with_what_is_beneath(self):
        css = '.base{background:#0C3D1C} .chip{background:rgba(255,255,255,.9);color:#fff}'
        f = run(css, '<div class="base"><span class="chip">white on near-white chip</span></div>')
        self.assertEqual(texts(f), ['white on near-white chip'])
        css = '.base{background:#0C3D1C} .chip{background:rgba(128,128,128,.08);color:#F0A81E}'
        self.assertEqual(run(css, '<div class="base"><span class="chip">gold on dark chip</span></div>'), [])

    def test_gradient_uses_the_worst_colour_stop(self):
        css = '.hero{background:linear-gradient(160deg,#0C3D1C 0%,#d0e0d0 100%);color:#fff}'
        f = run(css, '<div class="hero"><p>white over a gradient that turns pale</p></div>')
        self.assertEqual(len(f), 1)
        css = '.hero{background:linear-gradient(160deg,#123D20,#0C3D1C 60%,#101010);color:#fff}'
        self.assertEqual(run(css, '<div class="hero"><p>fine</p></div>'), [])

    def test_opacity_lowers_contrast(self):
        css = '.dim{opacity:.4;color:#101010}'
        f = run(css, '<p class="dim">faded text</p><p style="opacity:.9;color:#101010">barely faded</p>')
        self.assertEqual(texts(f), ['faded text'])

    def test_opacity_zero_is_treated_as_an_animation_start_state_not_a_failure(self):
        self.assertEqual(run('.reveal{opacity:0;color:#101010}', '<p class="reveal">appears on scroll</p>'), [])

    def test_large_text_needs_only_3_to_1(self):
        # #8a8a8a on white is ~3.4:1
        f = run('', '<p style="color:#8a8a8a;font-size:14px">small</p><h2 style="color:#8a8a8a;font-size:32px">large heading</h2>')
        self.assertEqual(texts(f), ['small'])
        f = run('', '<p style="color:#8a8a8a;font-size:19px;font-weight:700">bold 19px counts as large</p>')
        self.assertEqual(f, [])
        f = run('h3{font-size:18px}', '<h3 style="color:#8a8a8a">18px bold is NOT large</h3>')
        self.assertEqual(len(f), 1)

    def test_clamp_uses_the_smallest_size(self):
        # ~3.4:1 colour. Normal text needs 4.5, large text (>=24px) needs 3.
        f = run('', '<p style="color:#8a8a8a;font-size:clamp(14px,5vw,52px)">smallest clamp value is 14px</p>')
        self.assertEqual(len(f), 1)
        f = run('', '<p style="color:#8a8a8a;font-size:clamp(30px,5vw,52px)">smallest clamp value is 30px</p>')
        self.assertEqual(f, [])

    def test_hidden_elements_are_skipped(self):
        css = '.gone{display:none;color:#fff}'
        self.assertEqual(run(css, '<p class="gone">hidden</p><p hidden style="color:#fff">hidden attr</p>'), [])

    def test_hover_and_pseudo_element_rules_do_not_affect_resting_state(self):
        css = 'a{color:#101010} a:hover{color:#fff} a::before{color:#fff}'
        self.assertEqual(run(css, '<a href="#">link</a>'), [])

    def test_floating_header_is_checked_in_both_states(self):
        css = ('header.site-header{position:fixed;color:#fff} header.site-header.is-light{color:#101010}'
               'header.site-header.is-scrolled{background:rgba(255,255,255,.86);color:#101010}'
               '.hero{background:#0C3D1C;color:#fff;padding:90px}')
        body = '<header class="site-header"><a>Nav</a></header><section class="hero" data-theme="dark"><h1>Hello</h1></section>'
        self.assertEqual(run(css, body, ''), [], 'white over dark hero, then dark on white bar')

    def test_header_bug_white_text_on_white_when_scrolled_is_caught(self):
        css = ('header.site-header{position:fixed;color:#fff}'
               'header.site-header.is-scrolled{background:rgba(255,255,255,.86)}'   # forgot to switch text colour
               '.hero{background:#0C3D1C;color:#fff}')
        body = '<header class="site-header"><a>Nav</a></header><section class="hero"><h1>Hello</h1></section>'
        f = run(css, body, '')
        self.assertTrue(any('scrolled' in x['where'] for x in f), f)

    def test_header_bug_white_text_over_a_LIGHT_first_section_is_caught(self):
        # the exact class of bug reported: a page whose hero is light but whose header text stays white
        css = ('header.site-header{color:#fff} header.site-header.is-light{color:#fff}'  # broken light state
               '.hero{background:#F5F7F3;color:#101010}')
        body = '<header class="site-header"><a>Nav</a></header><section class="hero"><h1>Light hero</h1></section>'
        f = run(css, body)
        self.assertTrue(any('top' in x['where'] for x in f), f)

    def test_child_combinator_only_matches_direct_children(self):
        css = '.card{background:#0C3D1C} .card > h3{color:#fff} .card h3 em{color:#101010}'
        body = '<div class="card"><h3>direct child is white <em>nested dark</em></h3><div><h3>not a direct child</h3></div></div>'
        self.assertEqual(texts(run(css, body)), ['nested dark', 'not a direct child'])

    def test_not_and_attribute_selectors(self):
        css = 'section[data-theme="dark"]{background:#0C3D1C;color:#fff} section[data-theme="dark"]:not(.hero){background:#F5F7F3}'
        body = '<section data-theme="dark" class="hero"><p>hero stays dark</p></section><section data-theme="dark"><p>white text now on pale bg</p></section>'
        self.assertEqual(texts(run(css, body)), ['white text now on pale bg'])

    def test_background_shorthand_replaces_an_earlier_background_color(self):
        css = '.x{background-color:#0C3D1C;color:#fff} .x{background:#fff}'
        self.assertEqual(texts(run(css, '<p class="x">white on white</p>')), ['white on white'])

    def test_text_color_with_alpha(self):
        css = '.base{background:#fff} .t{color:rgba(16,16,16,.2)}'
        self.assertEqual(texts(run(css, '<div class="base"><p class="t">too faint</p></div>')), ['too faint'])

    def test_header_text_white_over_dark_bar_when_scrolled_over_dark_section(self):
        css = ('header.site-header{position:fixed;color:#fff} header.site-header.is-light{color:#101010}'
               'header.site-header.is-scrolled{background:rgba(255,255,255,.86);color:#101010}'
               'header.site-header.is-scrolled.is-dark-bg{background:rgba(14,20,12,.82);color:#fff}'
               'section.d{background:#0C3D1C;color:#fff}')
        body = '<header class="site-header"><a>Nav</a></header><section class="d"><h1>x</h1></section>'
        self.assertEqual(run(css, body, ''), [])
        # the same page with the dark-bar rule's text colour forgotten must be caught
        bad = css.replace('background:rgba(14,20,12,.82);color:#fff', 'background:rgba(14,20,12,.82)')
        f = run(bad, body, '')
        self.assertTrue(any('scrolled-dark' in x['where'] for x in f), f)

    def test_stale_data_start_dark_attribute_no_longer_matters(self):
        css = ('header.site-header{color:#fff} header.site-header.is-light{color:#101010} section.h{background:#F5F7F3}')
        body = '<header class="site-header"><a>Nav</a></header><section class="h"><h1>Light hero</h1></section>'
        # the page script now derives the starting colour from the real first section, so a stale attribute is harmless
        self.assertEqual(run(css, body, 'data-start-dark'), [])
        self.assertEqual(run(css, body, ''), [])

if __name__ == '__main__':
    unittest.main(verbosity=2)
