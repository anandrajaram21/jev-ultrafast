(() => {
  if (!document.body) return null;
  const cache = window.__jevFast ||= {ids:new WeakMap(), nodes:new Map(), next:1};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  for (const [id,e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  const safe = e => !['password','file','hidden'].includes(e.type);
  const visible = e => !e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(e.ownerDocument.getElementById(id),seen)).filter(Boolean).join(' ');
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels||[])].map(l=>name(l,seen)).filter(Boolean).join(' ') ||
      (['button','submit','reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName==='INPUT' ? '' : [...e.childNodes].map(n=>n.nodeType===3 ? n.textContent :
        n.nodeType===1 && n.getAttribute('aria-hidden')!=='true' ? name(n,seen) : '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '';
  };
  const roles=['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton'];
  const selector='a[href],button,input,textarea,select,summary,[contenteditable="true"],'+
    roles.map(role=>'[role="'+role+'"]').join(',');
  const role = e => {
    const explicit=e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (e.tagName==='BUTTON' || e.tagName==='SUMMARY') return 'button';
    if (e.tagName==='A') return 'link';
    if (e.tagName==='SELECT') return 'combobox';
    if (e.tagName==='TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName==='INPUT') {
      if (['checkbox','radio'].includes(e.type)) return e.type;
      if (['button','submit','reset','image'].includes(e.type)) return 'button';
      if (e.type==='search') return 'searchbox';
      if (e.type==='number') return 'spinbutton';
      if (['text','email','url','tel'].includes(e.type)) return 'textbox';
    }
    return null;
  };
  // Same-origin iframes, such as the block editor canvas, are read in place with their offset and
  // visible clip. Cross-origin frames stay opaque.
  const inset = f => {
    const s=f.ownerDocument.defaultView.getComputedStyle(f);
    return [f.clientLeft+parseFloat(s.paddingLeft), f.clientTop+parseFloat(s.paddingTop)];
  };
  cache.frames=()=>{
    const frames=[], add=(doc,ox,oy,clip)=>{
      frames.push({doc,win:doc.defaultView,ox,oy,clip});
      for (const f of doc.querySelectorAll('iframe')) {
        let inner=null;
        try { inner=f.contentDocument; } catch {}
        if (!inner?.body || !visible(f)) continue;
        const r=f.getBoundingClientRect(), [dx,dy]=inset(f), x=ox+r.x+dx, y=oy+r.y+dy;
        const box={l:Math.max(clip.l,x),t:Math.max(clip.t,y),
          r:Math.min(clip.r,x+inner.defaultView.innerWidth),b:Math.min(clip.b,y+inner.defaultView.innerHeight)};
        if (box.r>box.l && box.b>box.t) add(inner,x,y,box);
      }
    };
    add(document,0,0,{l:0,t:0,r:innerWidth,b:innerHeight});
    return frames;
  };
  // Top-level center of e, or null unless it is the hit target in its own frame and every parent frame.
  cache.point=e=>{
    let doc=e.ownerDocument, target=e;
    const r=e.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    let x=r.x+r.width/2, y=r.y+r.height/2;
    for (;;) {
      const win=doc.defaultView;
      if (x<0 || y<0 || x>=win.innerWidth || y>=win.innerHeight || !target.contains(doc.elementFromPoint(x,y)))
        return null;
      if (doc===document) return {x,y};
      const f=win.frameElement;
      if (!f) return null;
      const fr=f.getBoundingClientRect(), [dx,dy]=inset(f);
      x+=fr.x+dx; y+=fr.y+dy; target=f; doc=f.ownerDocument;
    }
  };
  cache.pageKey=()=>{
    const frames=cache.frames();
    return [performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
      frames.flatMap(({doc})=>[...doc.querySelectorAll('input,textarea,select')]).filter(safe)
        .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly]),
      frames.slice(1).map(({win})=>[win.performance.timeOrigin,win.location.href,win.scrollX,win.scrollY,
        win.innerWidth,win.innerHeight])];
  };
  cache.guard=(e,texts=new Map())=>{
    if (!e?.isConnected || !visible(e)) return null;
    const scope=e.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || e.parentElement;
    // innerText forces layout; controls sharing a row or form read it once per snapshot.
    if (scope && !texts.has(scope)) texts.set(scope,scope.innerText?.slice(0,6000)||'');
    return [identity(e),role(e),name(e),e.value??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('href'),scope ? texts.get(scope) : ''];
  };
  // The nearest heading of an enclosing group, so lookalike controls ("Authors" box vs. menu) differ.
  const heading=':scope>legend,:scope>h1,:scope>h2,:scope>h3,:scope>h4,:scope>[role="heading"],'+
    ':scope>*>h2,:scope>*>h3,:scope>*>[role="heading"]';
  // Memoized per ancestor: controls in one box share the work. A heading inside the box names it.
  const headings=new Map();
  const headingOf = a => {
    if (!headings.has(a)) {
      const h=[...a.querySelectorAll(heading)].find(h=>{
        const r=h.getBoundingClientRect();
        return visible(h) && r.width>2 && r.height>2;
      });
      headings.set(a,h ? [h,h.innerText.trim().replace(/\s+/g,' ').slice(0,60)] : null);
    }
    return headings.get(a);
  };
  const section = e => {
    for (let a=e.parentElement, i=0; a && i<8; a=a.parentElement, i++) {
      const h=headingOf(a);
      if (h && h[1] && !h[0].contains(e)) return h[1];
    }
    return '';
  };
  // The top document is read whole: controls below or above the view are offered and scrolled to on use.
  // Controls parked off the page (e.g. table row actions shown on hover or focus) are offered when their
  // row is on the page, and revealed by focus on use. Frames stay clipped to their visible area.
  const pageW=document.documentElement.scrollWidth, pageH=document.documentElement.scrollHeight;
  const actions=[], frames=cache.frames();
  for (const {doc,ox,oy,clip} of frames) for (const e of doc.querySelectorAll(selector)) {
    if (!safe(e) || !visible(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
    const r=e.getBoundingClientRect(), x=ox+r.x+r.width/2, y=oy+r.y+r.height/2, rname=role(e);
    if (!rname || r.width<=0 || r.height<=0) continue;
    let offscreen=null;
    if (x<clip.l || y<clip.t || x>=clip.r || y>=clip.b) {
      if (doc!==document) continue;
      const px=x+scrollX, py=y+scrollY;
      if (px>=0 && py>=0 && px<pageW && py<pageH) offscreen=y<0 ? 'above' : y>=innerHeight ? 'below' : 'beside';
      else {
        const row=e.closest('tr,[role="row"]')?.getBoundingClientRect();
        if (!row || !row.height || row.bottom+scrollY<=0 || row.top+scrollY>=pageH) continue;
        offscreen=row.top<0 ? 'above' : row.top>=innerHeight ? 'below' : 'hidden until focused';
      }
    }
    if (rname==='gridcell' && e.querySelector('button,[role="button"]')) continue;
    const base={node:identity(e),role:rname,label:name(e)||rname,
      rect:{x:ox+r.x,y:oy+r.y,w:r.width,h:r.height}};
    const group=section(e);
    if (group && group!==base.label) base.section=group;
    if (offscreen) base.offscreen=offscreen;
    for (const key of ['checked','selected','expanded']) {
      const value=e.getAttribute('aria-'+key);
      if (value!==null) base[key]=value;
    }
    if (['checkbox','radio'].includes(e.type)) base.checked=String(e.checked);
    if (e.tagName==='SELECT') {
      for (const o of e.options) if (!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
        actions.push({...base,kind:'select',value:o.value,
          current_value:[...e.selectedOptions].map(o=>o.label).join(', '),label:base.label+' → '+o.label});
    } else {
      const editable=!e.readOnly && e.getAttribute('aria-readonly')!=='true' &&
        (['textbox','searchbox','spinbutton'].includes(rname) ||
          (rname==='combobox' && ['INPUT','TEXTAREA'].includes(e.tagName)));
      const value='value' in e ? String(e.value) :
        e.isContentEditable || rname==='combobox' ? e.innerText.trim() : '';
      actions.push({...base,kind:editable?'fill':'click',value});
      if (editable) actions.push({...base,kind:'click',value,label:'Open '+base.label});
    }
  }
  const words=[]; let length=0;
  for (const {doc,ox,oy,clip} of frames) {
    const walker=doc.createTreeWalker(doc.body,NodeFilter.SHOW_TEXT), range=doc.createRange();
    let node;
    while ((node=walker.nextNode()) && length<6000) {
      const value=node.textContent.trim(), parent=node.parentElement;
      if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
      range.selectNodeContents(node); const r=range.getBoundingClientRect();
      if (r.width>0 && r.height>0 && oy+r.bottom>clip.t && oy+r.top<clip.b && ox+r.right>clip.l && ox+r.left<clip.r) {
        words.push(value); length+=value.length;
      }
    }
  }
  const text=words.join('\n').slice(0,6000);
  // Scroll the top page; if it cannot scroll, scroll the largest visible frame that can (the editor canvas).
  const scrolls=({win})=>win.scrollY+win.innerHeight<win.document.documentElement.scrollHeight-2 || win.scrollY>0;
  const area=({clip})=>(clip.r-clip.l)*(clip.b-clip.t);
  const scroller=scrolls(frames[0]) ? frames[0] : frames.slice(1).filter(scrolls).sort((a,b)=>area(b)-area(a))[0] ||
    frames[0];
  const {win}=scroller, height=win.document.documentElement.scrollHeight;
  const point=scroller===frames[0] ? {} :
    {point:{x:(scroller.clip.l+scroller.clip.r)/2,y:(scroller.clip.t+scroller.clip.b)/2}};
  const page_key=cache.pageKey(), guards={}, texts=new Map();
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node),texts);
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,page_key[6],page_key[7]];
  // An offscreen copy of a control already in view (a second toolbar under a table) only adds ambiguity.
  const inView=new Set(actions.filter(a=>!a.offscreen).map(a=>[a.kind,a.role,a.label,a.section].join('|')));
  for (let i=actions.length-1; i>=0; i--)
    if (actions[i].offscreen && inView.has([actions[i].kind,actions[i].role,actions[i].label,actions[i].section].join('|')))
      actions.splice(i,1);
  // Keep every control in view and only the 60 nearest offscreen ones: each candidate costs decision time.
  // Controls revealed by focus count as half a screen away, so nearby boxes below the fold come first.
  const distance=a=>!a.offscreen ? 0 : a.offscreen==='hidden until focused' ? innerHeight/2 :
    a.rect.y<0 ? -(a.rect.y+a.rect.h) : Math.max(0,a.rect.y-innerHeight);
  actions.forEach((a,i)=>a.order=i);
  actions.sort((a,b)=>!b.offscreen-!a.offscreen || distance(a)-distance(b) || a.order-b.order);
  const limit=Math.min(250,actions.filter(a=>!a.offscreen).length+60);
  const omitted_actions=Math.max(0,actions.length-limit);
  actions.splice(limit);
  actions.sort((a,b)=>a.order-b.order);
  actions.forEach(a=>delete a.order);
  actions.forEach((a,i)=>a.id='e'+(i+1));
  if (win.scrollY+win.innerHeight<height-2)
    actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:560,...point});
  if (win.scrollY>0) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-560,...point});
  let focused=document.activeElement;
  while (focused?.contentDocument?.activeElement) focused=focused.contentDocument.activeElement;
  if (focused && focused.matches?.('input,textarea,[contenteditable="true"]') && safe(focused) &&
      ['textbox','searchbox','combobox','spinbutton'].includes(role(focused)) && focused.tagName!=='TEXTAREA')
    actions.push({id:'press_enter',kind:'key',key:'Enter',label:'Press Enter in '+(name(focused)||role(focused))});
    actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    scroll:{y:win.scrollY,height},actions,marker,page_key,guards,omitted_actions};
})()
