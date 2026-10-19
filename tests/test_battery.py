"""GitHub render battery (6xl.87): every construct GitHub's gfm renderer shows no text of must leave the review.

The table was rendered on 2026-10-19 with `gh api -X POST /markdown -f mode=gfm -f text=...` (authenticated);
the third field records whether the token was visible text in GitHub's HTML. Hidden constructs are asserted
removed from strip_hidden (tree/, hidden-names/, conventions/), strip_hidden_diff (change.diff) and
sanitize_prose (spec.md); visible ones are listed in untrusted-input.md. Re-render with the same command to
refresh the verdicts when GitHub's renderer changes.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "review-code",
                                "scripts"))

import sanitize  # noqa: E402

BATTERY = [
    ('comment', 'a <!-- TOKCOMMENT --> b', False),
    ('comment_multiline', 'a\n\n<!--\nTOKCOMMENTMULTILINE\n-->\n\nb', False),
    ('comment_unclosed', 'a\n\n<!-- TOKCOMMENTUNCLOSED\n\nb', False),
    ('comment_dashes', 'a <!--- TOKCOMMENTDASHES ---> b', True),
    ('comment_short', 'a\n\n<!-->TOKCOMMENTSHORT\n\nb', True),
    ('bogus_bang_digit', '<div>\n<!1TOKBOGUSBANGDIGIT>\n</div>', False),
    ('bogus_bang_dash', '<div>\n<!-TOKBOGUSBANGDASH>\n</div>', False),
    ('bogus_bang_letter', '<div>\n<!xTOKBOGUSBANGLETTER>\n</div>', False),
    ('bogus_end_space', '<div>\n</ TOKBOGUSENDSPACE>\n</div>', False),
    ('bogus_end_digit', '<div>\n</1TOKBOGUSENDDIGIT>\n</div>', False),
    ('end_tag_attr', '<div>\n<p>x</p title="TOKENDTAGATTR">\n</div>', False),
    ('doctype', 'a\n\n<!DOCTYPE TOKDOCTYPE>\n\nb', False),
    ('doctype_lower', 'a\n\n<!doctype html TOKDOCTYPELOWER>\n\nb', True),
    ('cdata', 'a\n\n<![CDATA[TOKCDATA]]>\n\nb', False),
    ('pi', 'a\n\n<?x TOKPI ?>\n\nb', False),
    ('pi_xml', 'a\n\n<?xml version="TOKPIXML"?>\n\nb', False),
    ('inline_bang', 'a <!TOKINLINEBANG> b', True),
    ('inline_pi', 'a <?TOKINLINEPI?> b', False),
    ('refcomment_hash', 'a\n\n[//]: # (TOKREFCOMMENTHASH)\n\nb', False),
    ('refcomment_angle', 'a\n\n[//]: <> (TOKREFCOMMENTANGLE)\n\nb', False),
    ('refcomment_quote', 'a\n\n[comment]: # "TOKREFCOMMENTQUOTE"\n\nb', False),
    ('refcomment_blockquote', '> [//]: # (TOKREFCOMMENTBLOCKQUOTE)', False),
    ('refcomment_list', '- [//]: # (TOKREFCOMMENTLIST)', False),
    ('linkdef', 'a\n\n[r1]: http://e.com/TOKLINKDEF "TOKLINKDEF"\n\nb', False),
    ('linkdef_angle', "a\n\n[r1]: <http://e.com/TOKLINKDEFANGLE> 'TOKLINKDEFANGLE'\n\nb", False),
    ('linkdef_paren_title', 'a\n\n[r1]: http://e.com (TOKLINKDEFPARENTITLE)\n\nb', False),
    ('linkdef_next_dest', 'a\n\n[r1]:\nhttp://e.com/TOKLINKDEFNEXTDEST\n\nb', False),
    ('linkdef_next_title', 'a\n\n[r1]: http://e.com\n"TOKLINKDEFNEXTTITLE"\n\nb', False),
    ('linkdef_multiline_title', 'a\n\n[r1]: http://e.com "TOKLINKDEFMULTILINETITLE\nmore TOKLINKDEFMULTILINETITLE"\n\nb', False),
    ('linkdef_label_only_hash', 'a\n\n[TOKLINKDEFLABELONLYHASH]: #\n\nb', False),
    ('linkdef_escaped_label', 'a\n\n[a\\]TOKLINKDEFESCAPEDLABEL]: u "x"\n\nb', False),
    ('linkdef_indent3', 'a\n\n   [r1]: http://e.com/TOKLINKDEFINDENT3\n\nb', False),
    ('linkdef_blockquote', '> [r1]: http://e.com "TOKLINKDEFBLOCKQUOTE"', False),
    ('linkdef_list', '- [r1]: http://e.com "TOKLINKDEFLIST"', False),
    ('linkdef_ordered', '1. [r1]: http://e.com "TOKLINKDEFORDERED"', False),
    ('linkdef_nested', '> - [r1]: http://e.com "TOKLINKDEFNESTED"', False),
    ('linkdef_ref_in_codespan', 'see `[r2]`\n\n[r2]: http://e.com "TOKLINKDEFREFINCODESPAN"', False),
    ('linkdef_ref_in_comment', '<!-- [r2] -->\n\n[r2]: http://e.com "TOKLINKDEFREFINCOMMENT"', False),
    ('linkdef_ref_escaped', 'see \\[r2\\]\n\n[r2]: http://e.com "TOKLINKDEFREFESCAPED"', False),
    ('linkdef_ref_in_attr', '<span title="[r2]">t</span>\n\n[r2]: http://e.com "TOKLINKDEFREFINATTR"', False),
    ('linkdef_ref_in_dest', '[t](http://e.com/[r2])\n\n[r2]: http://e.com "TOKLINKDEFREFINDEST"', False),
    ('linkdef_ref_in_fence', '```\n[r2]\n```\n\n[r2]: http://e.com "TOKLINKDEFREFINFENCE"', False),
    ('linkdef_title_self', '[r2]: http://e.com "[r2] TOKLINKDEFTITLESELF"', False),
    ('linkdef_used_title', 'a [t][r3]\n\n[r3]: http://e.com "TOKLINKDEFUSEDTITLE"', False),
    ('linkdef_used_dest', 'a [t][r3]\n\n[r3]: http://e.com/TOKLINKDEFUSEDDEST', False),
    ('footdef', 'a\n\n[^f1]: TOKFOOTDEF\n\nb', False),
    ('footdef_lazy', 'a\n\n[^f1]: x\nTOKFOOTDEFLAZY lazy\n\nb', False),
    ('footdef_indented_para', 'a\n\n[^f1]: x\n\n    TOKFOOTDEFINDENTEDPARA para\n\nb', False),
    ('footdef_blockquote', '> [^f1]: TOKFOOTDEFBLOCKQUOTE', False),
    ('footdef_list', '- [^f1]: TOKFOOTDEFLIST', False),
    ('footdef_escaped_label', 'a\n\n[^a\\]TOKFOOTDEFESCAPEDLABEL]: x\n\nb', False),
    ('footdef_ref_in_codespan', 'see `[^f2]`\n\n[^f2]: TOKFOOTDEFREFINCODESPAN', False),
    ('footdef_ref_in_comment', '<!-- [^f2] -->\n\n[^f2]: TOKFOOTDEFREFINCOMMENT', False),
    ('footdef_used', 'a[^f3]\n\n[^f3]: TOKFOOTDEFUSED', True),
    ('footref_label', 'a[^TOKFOOTREFLABEL]\n\n[^TOKFOOTREFLABEL]: x', False),
    ('reflabel_link', 'a [here][TOKREFLABELLINK]\n\n[TOKREFLABELLINK]: http://e.com', False),
    ('reflabel_img', 'a ![a][TOKREFLABELIMG]\n\n[TOKREFLABELIMG]: http://e.com/i.png', False),
    ('reflabel_collapsed', 'a [TOKREFLABELCOLLAPSED][]\n\n[TOKREFLABELCOLLAPSED]: http://e.com', True),
    ('reflabel_shortcut', 'a [TOKREFLABELSHORTCUT]\n\n[TOKREFLABELSHORTCUT]: http://e.com', True),
    ('img_alt', 'a ![TOKIMGALT](u.png) b', False),
    ('img_alt_ref', 'a ![TOKIMGALTREF][r]\n\n[r]: u.png', False),
    ('img_alt_nested', 'a ![x [TOKIMGALTNESTED] y](u.png) b', False),
    ('img_title', 'a ![x](u.png "TOKIMGTITLE") b', False),
    ('link_title', 'a [t](u "TOKLINKTITLE") b', False),
    ('link_dest', 'a [t](http://e.com/TOKLINKDEST) b', False),
    ('link_empty_text', 'a [](http://e.com/TOKLINKEMPTYTEXT) b', False),
    ('autolink', 'a <http://e.com/TOKAUTOLINK> b', True),
    ('autolink_email', 'a <TOKAUTOLINKEMAIL@e.com> b', True),
    ('bare_url', 'a http://e.com/TOKBAREURL b', True),
    ('html_img_alt', 'a <img alt="TOKHTMLIMGALT" src="u.png"> b', False),
    ('html_img_title', 'a <img title="TOKHTMLIMGTITLE" src="u.png"> b', False),
    ('html_img_width', 'a <img width="TOKHTMLIMGWIDTH" src="u.png"> b', False),
    ('html_img_src', 'a <img src="http://e.com/TOKHTMLIMGSRC.png"> b', False),
    ('html_img_src_js', 'a <img src="javascript:TOKHTMLIMGSRCJS"> b', False),
    ('html_a_href', 'a <a href="http://e.com/TOKHTMLAHREF">t</a> b', False),
    ('html_a_href_js', 'a <a href="javascript:TOKHTMLAHREFJS">t</a> b', False),
    ('html_a_name', 'a <a name="TOKHTMLANAME">t</a> b', False),
    ('html_a_id', 'a <a id="TOKHTMLAID">t</a> b', False),
    ('html_p_title', '<p title="TOKHTMLPTITLE">x</p>', False),
    ('html_p_lang', '<p lang="TOKHTMLPLANG">x</p>', False),
    ('html_p_dir', '<p dir="TOKHTMLPDIR">x</p>', False),
    ('html_p_class', '<p class="TOKHTMLPCLASS">x</p>', False),
    ('html_p_style', '<p style="color: TOKHTMLPSTYLE">x</p>', False),
    ('html_p_data', '<p data-x="TOKHTMLPDATA">x</p>', False),
    ('html_p_aria', '<p aria-label="TOKHTMLPARIA">x</p>', False),
    ('html_p_valueless', '<p TOKHTMLPVALUELESS>x</p>', False),
    ('html_p_unknown_valued', '<p TOKHTMLPUNKNOWNVALUED="1">x</p>', False),
    ('html_p_align', '<p align="TOKHTMLPALIGN">x</p>', False),
    ('html_table_border', '<table border="TOKHTMLTABLEBORDER"><tr><td>x</td></tr></table>', False),
    ('html_td_colspan', '<table><tr><td colspan="TOKHTMLTDCOLSPAN">x</td></tr></table>', False),
    ('html_source_srcset', '<picture><source srcset="TOKHTMLSOURCESRCSET"><img src="u.png"></picture>', False),
    ('html_ol_start', '<ol start="TOKHTMLOLSTART"><li>x</li></ol>', False),
    ('html_details_open', '<details open="TOKHTMLDETAILSOPEN"><summary>s</summary>x</details>', False),
    ('html_input_value', 'a <input value="TOKHTMLINPUTVALUE"> b', False),
    ('html_input_placeholder', 'a <input placeholder="TOKHTMLINPUTPLACEHOLDER"> b', False),
    ('html_area_alt', '<map><area alt="TOKHTMLAREAALT"></map>', False),
    ('html_meta', 'a <meta content="TOKHTMLMETA"> b', False),
    ('html_abbr_title', 'a <abbr title="TOKHTMLABBRTITLE">x</abbr> b', False),
    ('html_span_title_unquoted', 'a <span title=TOKHTMLSPANTITLEUNQUOTED>t</span> b', False),
    ('html_unknown_tag_attr', 'a <foo bar="TOKHTMLUNKNOWNTAGATTR">t</foo> b', False),
    ('html_unknown_tag_name', 'a <TOKHTMLUNKNOWNTAGNAME>t</TOKHTMLUNKNOWNTAGNAME> b', False),
    ('html_unknown_selfclose', 'a <TOKHTMLUNKNOWNSELFCLOSE/> b', False),
    ('html_custom_element', 'a <x-TOKHTMLCUSTOMELEMENT>t</x-TOKHTMLCUSTOMELEMENT> b', False),
    ('html_hidden_attr', 'a <div hidden>TOKHTMLHIDDENATTR</div> b', True),
    ('html_display_none', 'a <span style="display:none">TOKHTMLDISPLAYNONE</span> b', True),
    ('html_font_size0', 'a <span style="font-size:0">TOKHTMLFONTSIZE0</span> b', True),
    ('html_details_body', '<details><summary>s</summary>TOKHTMLDETAILSBODY</details>', True),
    ('html_summary', '<details><summary>TOKHTMLSUMMARY</summary>x</details>', True),
    ('html_kbd', 'a <kbd>TOKHTMLKBD</kbd> b', True),
    ('html_sub', 'a <sub>TOKHTMLSUB</sub> b', True),
    ('html_script', 'a\n\n<script>TOKHTMLSCRIPT</script>\n\nb', True),
    ('html_style', 'a\n\n<style>TOKHTMLSTYLE</style>\n\nb', True),
    ('html_noscript', 'a <noscript>TOKHTMLNOSCRIPT</noscript> b', True),
    ('html_template', 'a <template>TOKHTMLTEMPLATE</template> b', True),
    ('html_textarea', 'a <textarea>TOKHTMLTEXTAREA</textarea> b', True),
    ('html_iframe', 'a <iframe>TOKHTMLIFRAME</iframe> b', True),
    ('html_svg_title', 'a <svg><title>TOKHTMLSVGTITLE</title></svg> b', True),
    ('html_object', 'a <object data="TOKHTMLOBJECT">t</object> b', False),
    ('html_block_attr', '<div title="TOKHTMLBLOCKATTR">\n\nx\n\n</div>', False),
    ('html_ins_datetime', 'a <ins datetime="TOKHTMLINSDATETIME">x</ins> b', False),
    ('html_q_cite', 'a <q cite="TOKHTMLQCITE">x</q> b', False),
    ('html_entity_zwsp', 'a&#8203;TOKHTMLENTITYZWSP b', True),
    ('html_entity_comment', 'a &lt;!-- TOKHTMLENTITYCOMMENT --&gt; b', True),
    ('math_inline', 'a $TOKMATHINLINE$ b', True),
    ('codespan', 'a `<!-- TOKCODESPAN -->` b', True),
    ('fence', '```\n<!-- TOKFENCE -->\n```', True),
]


def token(name):
    """The payload token the battery put into a construct (same rule as when it was rendered)."""
    return "TOK" + name.upper().replace("_", "")[:24]


def diff_of(text):
    lines = text.split("\n")
    return ("diff --git a/n.md b/n.md\n--- a/n.md\n+++ b/n.md\n@@ -0,0 +1,%d @@\n" % len(lines)
            + "".join("+" + ln + "\n" for ln in lines))


# Kept on purpose (untrusted-input.md): a URL-shaped href or src and a markdown link destination are reviewable
# content a reader can follow, though GitHub shows them only on hover.
KEPT = {"html_a_href", "html_img_src", "link_dest", "link_empty_text"}


class Battery(unittest.TestCase):
    def test_battery_size(self):
        self.assertGreaterEqual(len(BATTERY), 119)

    def test_hidden_constructs_never_survive(self):
        for name, text, visible in BATTERY:
            if visible or name in KEPT:
                continue
            tok = token(name)
            with self.subTest(name):
                self.assertNotIn(tok, sanitize.strip_hidden(text)[0], "tree/")
                self.assertNotIn(tok, sanitize.strip_hidden_diff(diff_of(text))[0], "change.diff")
                self.assertNotIn(tok, sanitize.sanitize_prose(text), "spec.md")
                out = sanitize.strip_hidden(text)[0]
                self.assertEqual(out.count("\n"), text.count("\n"))


if __name__ == "__main__":
    unittest.main()
