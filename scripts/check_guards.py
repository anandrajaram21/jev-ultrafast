"""Local-browser freshness/execution regressions. No model calls or external websites."""

from urllib.parse import quote

from jev_ultrafast.browser import Browser, StalePage

HTML = """<!doctype html><title>Guard checks</title>
<style>body{margin:30px}button{width:180px;height:50px}#outside{position:absolute;top:3000px}</style>
<p id="context">Cart total: $10</p>
<button id="target" onclick="window.clicks=(window.clicks||0)+1">Continue</button>
<label>City<input id="field" value="Zurich"></label>
<label><input id="toggle" type="checkbox">Refundable</label>
<select aria-label="Category"><option>All</option><option>Design</option></select>
<p id="outside">Unrelated offscreen text</p>"""


def main():
    browser = Browser("data:text/html," + quote(HTML))
    passed = []
    try:
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Continue")
        browser.evaluate("document.querySelector('#target').style.transform='translateX(200px)'")
        assert browser.fresh(page), "Movement should use fresh geometry, not another model call"
        browser.act(action, page)
        assert browser.evaluate("window.clicks") == 1
        passed.append("moving target clicked at its current location")

        browser.evaluate("document.querySelector('#outside').textContent='Updated outside the viewport'")
        assert browser.fresh(page)
        passed.append("unrelated offscreen text does not invalidate")

        mutations = {
            "visible context": "document.querySelector('#context').textContent='Cart total: $100'",
            "accessible label": "document.querySelector('#target').setAttribute('aria-label','Delete account')",
            "field property": "document.querySelector('#field').value='London'",
            "checkbox property": "document.querySelector('#toggle').checked=true",
            "disabled target": "document.querySelector('#target').disabled=true",
            "read-only field": "document.querySelector('#field').readOnly=true",
            "hidden target": "document.querySelector('#target').style.display='none'",
            "replaced node": "document.querySelector('#target').outerHTML=document.querySelector('#target').outerHTML",
            "dropdown option": "document.querySelector('select').options[1].text='Coastal'",
        }
        for label, expression in mutations.items():
            browser.evaluate("document.querySelector('#target').style.display='block'; "
                             "document.querySelector('#target').disabled=false")
            page = browser.observe(screenshot=False)
            browser.evaluate(expression)
            assert not browser.fresh(page), label
            passed.append(label + " invalidates")

        browser.evaluate("document.querySelector('#target').disabled=false; "
                         "document.querySelector('#target').style.display='block'")
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Delete account")
        # A textless overlay does not alter the model's semantic state, but must block a click.
        browser.evaluate("const cover=document.createElement('div'); "
                         "cover.style.cssText='position:fixed;inset:0;z-index:9999;background:white'; "
                         "document.body.append(cover)")
        assert browser.fresh(page)
        try:
            browser.act(action, page)
        except (RuntimeError, StalePage):
            pass
        else:
            raise AssertionError("Covered target was clicked")
        assert browser.evaluate("window.clicks") == 1
        passed.append("overlay blocked before input")

        browser.evaluate("document.body.innerHTML=" + repr("""
          <form><p id="price">Total $10</p>
          <button type="button" id="buy">Buy</button>
          <label>Search <input id="query" role="combobox" aria-controls="suggestions"></label>
          <div role="listbox" id="suggestions"></div>
          <label><input id="check" type="checkbox">Enabled</label>
          <label><input id="radio" type="radio">Choice</label>
          <input id="readonly" aria-label="Read only" readonly>
          <input id="secret" type="password" value="never expose this">
          <button id="off" disabled>Disabled</button>
          <select id="category" aria-label="Category">
            <option>All</option><option>Design</option><option disabled>Unavailable</option>
          </select></form><aside id="unrelated">News</aside>
        """))
        page = browser.observe(screenshot=False)
        buy = next(a for a in page["actions"] if a["label"] == "Buy")
        browser.evaluate("document.querySelector('#unrelated').textContent='New unrelated news'")
        assert browser.fresh(page, buy)
        assert not browser.fresh(page)
        passed.append("click guard accepts unrelated visible updates; terminal guard rejects them")
        for label, expression in {
            "nearby price": "document.querySelector('#price').textContent='Total $100'",
            "form value": "document.querySelector('#query').value='changed'",
            "form toggle": "document.querySelector('#check').checked=true",
            "target replacement": "document.querySelector('#buy').outerHTML=document.querySelector('#buy').outerHTML",
        }.items():
            page = browser.observe(screenshot=False)
            buy = next(a for a in page["actions"] if a["label"] == "Buy")
            browser.evaluate(expression)
            assert not browser.fresh(page, buy), label
            passed.append(label + " invalidates action-specific guard")

        page = browser.observe(screenshot=False)
        actions = page["actions"]
        for role in ("checkbox", "radio"):
            assert {a["kind"] for a in actions if a.get("role") == role} == {"click"}
        assert {a["kind"] for a in actions if a["label"] == "Read only"} == {"click"}
        assert not any(a["label"] == "Disabled" or a.get("value") == "never expose this" for a in actions)
        assert [a["value"] for a in actions if a["kind"] == "select"] == ["Design"]
        passed.append("native controls expose only supported operations and safe values")

        select = next(a for a in actions if a["kind"] == "select")
        browser.act(select, page)
        assert browser.evaluate("document.querySelector('#category').value") == "Design"
        passed.append("native dropdown selects an observed option")

        browser.evaluate("document.querySelector('#query').addEventListener('input',()=>setTimeout(()=>{"
                         "document.querySelector('#suggestions').innerHTML='<div role=option>Generated</div>'"
                         "},60))")
        page = browser.observe(screenshot=False)
        field = next(a for a in page["actions"] if a["kind"] == "fill")
        browser.act(field, page, text="Generated")
        page = browser.observe(screenshot=False)
        value = browser.evaluate("document.querySelector('#query').value")
        assert value == "Generated", repr(value)
        assert any(a.get("role") == "option" for a in page["actions"])
        passed.append("real text input waits for asynchronous combobox suggestions")

        # A same-origin frame, like the block editor canvas: offset, hit-tested, typed into, and scrolled in place.
        browser.evaluate("document.body.innerHTML=" + repr(
            '<div style="height:120px"></div><iframe id="canvas" title="Editor canvas" '
            'style="width:600px;height:300px;border:4px solid;padding:6px"></iframe>'))
        browser.evaluate("(() => { const d=document.querySelector('#canvas').contentDocument; d.open(); d.write("
                         + repr('<p id="note">Draft saved locally</p>'
                                '<button id="save" onclick="window.clicks=(window.clicks||0)+1">Save draft</button>'
                                '<h1 contenteditable="true" aria-label="Add title"></h1>'
                                '<div style="height:2000px"></div><button>Far below</button>')
                         + "); d.close(); })()")
        frame = "document.querySelector('#canvas').contentWindow"
        page = browser.observe(screenshot=False)
        save = next(a for a in page["actions"] if a["label"] == "Save draft")
        assert save["rect"]["y"] > 120, save["rect"]
        assert "Draft saved locally" in page["text"]
        assert not any(a["label"] == "Far below" for a in page["actions"])
        passed.append("same-origin frame controls and text are observed at their page position")

        browser.act(save, page)
        assert browser.evaluate(frame + ".clicks") == 1
        passed.append("frame button clicked through its parent frame")

        page = browser.observe(screenshot=False)
        title = next(a for a in page["actions"] if a["kind"] == "fill" and a["label"] == "Add title")
        browser.act(title, page, text="Hello from Jev")
        assert browser.evaluate(frame + ".document.querySelector('h1').innerText") == "Hello from Jev"
        passed.append("frame contenteditable receives real text input")

        page = browser.observe(screenshot=False)
        save = next(a for a in page["actions"] if a["label"] == "Save draft")
        browser.evaluate(frame + ".document.querySelector('#note').textContent='Draft deleted'")
        assert not browser.fresh(page, save)
        passed.append("frame context change invalidates action-specific guard")

        page = browser.observe(screenshot=False)
        save = next(a for a in page["actions"] if a["label"] == "Save draft")
        browser.evaluate("(() => { const cover=document.createElement('div'); "
                         "cover.style.cssText='position:fixed;inset:0;z-index:9999'; document.body.append(cover); })()")
        try:
            browser.act(save, page)
        except (RuntimeError, StalePage):
            pass
        else:
            raise AssertionError("Covered frame target was clicked")
        assert browser.evaluate(frame + ".clicks") == 1
        browser.evaluate("document.body.lastElementChild.remove()")
        passed.append("parent overlay blocks frame input")

        page = browser.observe(screenshot=False)
        scroll = next(a for a in page["actions"] if a["id"] == "scroll_down")
        assert "point" in scroll
        browser.act(scroll, page)
        browser.observe(screenshot=False)
        assert browser.evaluate(frame + ".scrollY") > 0
        passed.append("an unscrollable page scrolls its editor frame")
        browser.call("Page.navigate", url="about:blank")
        assert not browser.fresh(page, field)
        passed.append("navigation invalidates the old document")
    finally:
        browser.close()
    print("\n".join(passed))
    print(f"PASS: {len(passed)} browser guard checks; no model calls")


if __name__ == "__main__":
    main()
