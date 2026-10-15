(() => {
  function init() {
    if (document.getElementById('meeting-clock-card')) return;

    const heading = Array.from(
      document.querySelectorAll('h1,h2,h3,h4,h5,h6')
    ).find(el => el.textContent.trim().toUpperCase() === 'ФОТОРАМКИ');

    if (!heading) {
      console.warn('TeleRepka: заголовок блока фоторамок не найден');
      return;
    }

    const oldCard = heading.parentElement;

    const style = document.createElement('style');
    style.textContent = `
      #meeting-clock-card {
        box-sizing: border-box;
        padding: 16px;
        border: 1px solid #30363d;
        border-radius: 12px;
        background: #161b22;
        min-width: 0;
      }
      #meeting-clock-card .clock-heading {
        margin: 0 0 14px;
        color: #8b949e;
        font: 700 14px/1.3 sans-serif;
        letter-spacing: 1px;
      }
      #meeting-clock-card .clock-display {
        display: block;
        width: 100%;
        height: auto;
        margin: 0 0 16px;
        border-radius: 8px;
        background: #0b0d10;
      }
      #meeting-clock-card .segment {
        fill: #ff9500;
      }
      #meeting-clock-card .segment.off {
        fill: #24190c;
      }
      #meeting-clock-card .meeting-link {
        display: flex;
        align-items: center;
        justify-content: center;
        box-sizing: border-box;
        width: 100%;
        min-height: 44px;
        padding: 10px 8px;
        border-radius: 9px;
        background: #ffd43b;
        color: #171717;
        text-decoration: none;
        font: 700 15px/1.4 sans-serif;
        text-align: center;
      }
      #meeting-clock-card .meeting-link:hover {
        background: #ffe066;
      }
      #meeting-clock-card .meeting-link:focus-visible {
        outline: 3px solid #fff;
        outline-offset: 3px;
      }
    `;
    document.head.appendChild(style);

    const card = document.createElement('section');
    card.id = 'meeting-clock-card';

    card.innerHTML = `
      <h3 class="clock-heading">ВРЕМЯ</h3>
      <svg class="clock-display" viewBox="0 0 452 120"
           role="img" aria-label="Текущее время"></svg>
      <a class="meeting-link"
         href="https://cloud-b.istu.edu/aux/"
         target="_blank" rel="noopener noreferrer">
        Протоколы совещаний
      </a>
    `;

    card.querySelector('.meeting-link').addEventListener('click', async (event) => {
      const isKiosk =
        new URLSearchParams(window.location.search).get('kiosk') === '1';

      if (isKiosk) event.preventDefault();

      try {
        const response = await fetch('/tv/open-meetings', {
          method: 'POST'
        });
        const result = await response.json();

        if (!response.ok || !result.ok) {
          throw new Error('Не удалось открыть протоколы на телевизоре');
        }
      } catch (error) {
        console.error('TeleRepka:', error);
        alert('Не удалось открыть протоколы на ТВ. Проверьте киоск.');
      }
    });

    oldCard.insertAdjacentElement('afterend', card);
    oldCard.hidden = true;
    oldCard.style.display = 'none';

    const svg = card.querySelector('svg');
    const ns = 'http://www.w3.org/2000/svg';

    const shapes = {
      a: '10,4 46,4 53,11 46,18 10,18 3,11',
      b: '48,20 55,13 55,49 48,56 41,49 41,27',
      c: '48,64 55,71 55,107 48,100 41,93 41,71',
      d: '10,102 46,102 53,109 46,116 10,116 3,109',
      e: '7,64 14,71 14,93 7,100 0,107 0,71',
      f: '7,20 14,27 14,49 7,56 0,49 0,13',
      g: '10,53 46,53 53,60 46,67 10,67 3,60'
    };

    const patterns = [
      'abcdef', 'bc', 'abdeg', 'abcdg', 'bcfg',
      'acdfg', 'acdefg', 'abc', 'abcdefg', 'abcdfg'
    ];

    const digits = [];
    const positions = [14, 79, 170, 235, 326, 391];

    for (const x of positions) {
      const group = document.createElementNS(ns, 'g');
      group.setAttribute('transform', `translate(${x},0)`);
      const segments = {};
      for (const [name, points] of Object.entries(shapes)) {
        const polygon = document.createElementNS(ns, 'polygon');
        polygon.setAttribute('points', points);
        polygon.setAttribute('class', 'segment off');
        group.appendChild(polygon);
        segments[name] = polygon;
      }
      svg.appendChild(group);
      digits.push(segments);
    }

    for (const x of [150, 306]) {
      for (const y of [36, 78]) {
        const dot = document.createElementNS(ns, 'rect');
        dot.setAttribute('x', x);
        dot.setAttribute('y', y);
        dot.setAttribute('width', '9');
        dot.setAttribute('height', '12');
        dot.setAttribute('rx', '2');
        dot.setAttribute('class', 'segment');
        svg.appendChild(dot);
      }
    }

    let previous = '';

    function tick() {
      const now = new Date();
      const time = [now.getHours(), now.getMinutes(), now.getSeconds()]
        .map(n => String(n).padStart(2, '0'))
        .join(':');
      const values = time.replaceAll(':', '');

      for (let i = 0; i < values.length; i++) {
        if (values[i] === previous[i]) continue;
        const active = patterns[Number(values[i])];
        for (const [name, polygon] of Object.entries(digits[i])) {
          polygon.setAttribute(
            'class', active.includes(name) ? 'segment' : 'segment off'
          );
        }
      }

      previous = values;
      svg.setAttribute('aria-label', `Текущее время ${time}`);
      setTimeout(tick, 1000 - (Date.now() % 1000));
    }

    tick();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, {once: true});
  } else {
    init();
  }
})();
