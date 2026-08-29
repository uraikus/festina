  // Mobile sidebar toggle
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('backdrop');
  const menuBtn = document.getElementById('menuBtn');
  function closeSidebar() {
    sidebar.classList.remove('open');
    backdrop.classList.remove('open');
  }
  menuBtn.addEventListener('click', () => {
    sidebar.classList.toggle('open');
    backdrop.classList.toggle('open');
  });
  backdrop.addEventListener('click', closeSidebar);
  document.getElementById('toc').addEventListener('click', (e) => {
    if (e.target.tagName === 'A') closeSidebar();
  });

  // Sidebar filter
  const filterBox = document.getElementById('navFilter');
  filterBox.addEventListener('input', () => {
    const q = filterBox.value.trim().toLowerCase();
    document.querySelectorAll('#toc a').forEach(a => {
      const match = !q || a.textContent.toLowerCase().includes(q);
      a.dataset.hidden = match ? 'false' : 'true';
    });
    document.querySelectorAll('.nav-group').forEach(g => {
      const anyVisible = [...g.querySelectorAll('a')].some(a => a.dataset.hidden !== 'true');
      g.style.display = anyVisible ? '' : 'none';
    });
  });

  // Scrollspy: highlight the section currently at the top of the
  // viewport. Deliberately a single scroll-driven calculation (not an
  // IntersectionObserver) so there's exactly one source of truth -- no
  // risk of two independent observers racing each other for a short
  // final section that a fixed rootMargin band might otherwise miss.
  const anchors = [...document.querySelectorAll('#toc a')];
  const byId = Object.fromEntries(anchors.map(a => [a.getAttribute('href').slice(1), a]));
  const targets = [...document.querySelectorAll('main [id]')]
    .filter(t => byId[t.id])
    .map(t => ({ id: t.id, el: t }));

  let ticking = false;
  function updateActive() {
    ticking = false;
    const offset = 80; // matches scroll-margin-top on headings
    const atBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
    let currentId = targets.length ? targets[0].id : null;
    if (atBottom) {
      currentId = targets[targets.length - 1].id;
    } else {
      for (const t of targets) {
        if (t.el.getBoundingClientRect().top <= offset) currentId = t.id;
        else break;
      }
    }
    anchors.forEach(a => a.classList.remove('active'));
    if (currentId && byId[currentId]) byId[currentId].classList.add('active');
  }
  function onScroll() {
    if (!ticking) {
      ticking = true;
      requestAnimationFrame(updateActive);
    }
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll);
  updateActive();
