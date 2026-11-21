/* Seasonal decorations for the Georgian holidays ("სეზონური დეკორაციები").
 *
 * Runs only in the browser, by the computer's date: the server does nothing
 * extra. Rules, so that nobody is disturbed at work:
 *   - decorations never catch clicks (pointer-events: none) and are not printed;
 *   - anything falling or flying lasts at most ~9 seconds and shows at most
 *     once every 30 minutes in a browser; a greeting shows once a day;
 *   - "reduce motion" in the system settings = nothing moves;
 *   - days of remembrance (9 April, 9 May) get a quiet sign only;
 *   - user menu -> "სეზონური დეკორაცია" switches everything off in this browser.
 * Preview any holiday: add ?season=<id> to the address (ids are in HOLIDAYS).
 */
(function () {
    'use strict';

    var KEY_OFF = 'medf-season-off';
    var KEY_LAST = 'medf-season-fall';
    var FALL_EVERY = 30 * 60 * 1000;

    // a test run of the New Year snow (ends by itself)
    var TEST = {id: 'newyear', from: '2026-11-21', to: '2026-11-22'};

    function store(get, key, value) {
        try {
            if (get) { return window.localStorage.getItem(key); }
            if (value === null) { window.localStorage.removeItem(key); } else { window.localStorage.setItem(key, value); }
        } catch (e) { return null; }
        return null;
    }

    /* Orthodox Easter (Julian computus + 13 days, valid 1900-2099) */
    function orthodoxEaster(year) {
        var a = year % 4, b = year % 7, c = year % 19;
        var d = (19 * c + 15) % 30, e = (2 * a + 4 * b - d + 34) % 7;
        var month = Math.floor((d + e + 114) / 31), day = ((d + e + 114) % 31) + 1;
        return new Date(year, month - 1, day + 13);
    }

    function md(date) { return (date.getMonth() + 1) * 100 + date.getDate(); }

    function between(date, m1, d1, m2, d2) {
        var v = md(date), from = m1 * 100 + d1, to = m2 * 100 + d2;
        return from <= to ? v >= from && v <= to : v >= from || v <= to;   // across New Year
    }

    function on(date, m, d) { return md(date) === m * 100 + d; }

    function easterOffset(date) {
        var day = new Date(date.getFullYear(), date.getMonth(), date.getDate());
        return Math.round((day - orthodoxEaster(date.getFullYear())) / 86400000);
    }

    /* small pictures next to the logo */
    var FLAG = '<svg viewBox="0 0 300 200" width="30" height="20" aria-hidden="true"><rect width="300" height="200" fill="#fff"/>' +
        '<rect x="130" width="40" height="200" fill="#E8112D"/><rect y="80" width="300" height="40" fill="#E8112D"/>' +
        '<g fill="#E8112D"><path id="szc" d="M58 23h8v15h15v8H66v15h-8V46H43v-8h15z"/>' +
        '<use href="#szc" x="176"/><use href="#szc" y="115"/><use href="#szc" x="176" y="115"/></g></svg>';
    var BADGES = {
        hat: '<svg viewBox="0 0 26 22" width="26" height="22" aria-hidden="true"><path d="M2 18 C6 6 14 1 22 6 L18 10 C14 8 10 12 9 18 Z" fill="#D7263D"/><rect x="0" y="16" width="14" height="5" rx="2.5" fill="#fff"/><circle cx="22" cy="6" r="3" fill="#fff"/></svg>',
        eggs: '<svg viewBox="0 0 30 20" width="30" height="20" aria-hidden="true"><ellipse cx="9" cy="11" rx="7" ry="9" fill="#C8102E"/><ellipse cx="22" cy="12" rx="6.5" ry="8" fill="#B42B5E"/><path d="M4 9q5 3 10 0M17 11q5 3 10 0" stroke="#fff" stroke-width="1.2" fill="none" opacity=".7"/></svg>',
        flag: FLAG,
        mourning: FLAG.replace('</svg>', '<path d="M300 0v70L230 0z" fill="#1B2437"/></svg>'),
        carnation: '<svg viewBox="0 0 22 26" width="20" height="24" aria-hidden="true"><path d="M11 12v13" stroke="#2E7D32" stroke-width="2"/><path d="M11 18l5-4" stroke="#2E7D32" stroke-width="1.6"/><path d="M4 8q1-6 7-6t7 6q-2 4-7 4T4 8z" fill="#C8102E"/><path d="M5 6l2 2 2-3 2 3 2-3 2 3 2-2" stroke="#8E0E22" stroke-width="1" fill="none"/></svg>',
        saltire: '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><circle cx="12" cy="12" r="11" fill="#fff"/><path d="M5 5l14 14M19 5L5 19" stroke="#1F4FB5" stroke-width="3.2" stroke-linecap="round"/></svg>',
        lily: '<svg viewBox="0 0 24 26" width="22" height="24" aria-hidden="true"><path d="M12 13v12" stroke="#2E7D32" stroke-width="2"/><path d="M12 13C8 12 5 8 6 3c3 1 5 4 6 7 1-3 3-6 6-7 1 5-2 9-6 10z" fill="#fff" stroke="#C9A227" stroke-width="1"/><path d="M12 13c-1-4 0-8 0-11 0 3 1 7 0 11z" fill="#F6E7B0"/></svg>',
        grapes: '<svg viewBox="0 0 24 28" width="21" height="25" aria-hidden="true"><path d="M12 6q2-5 7-5" stroke="#6B4F1D" stroke-width="2" fill="none"/><path d="M14 4q5 0 7 4-5 1-7-4z" fill="#4C9A2A"/><g fill="#5B2A86"><circle cx="7" cy="10" r="3.4"/><circle cx="13" cy="10" r="3.4"/><circle cx="19" cy="10" r="3.4"/><circle cx="10" cy="15.5" r="3.4"/><circle cx="16" cy="15.5" r="3.4"/><circle cx="13" cy="21" r="3.4"/></g></svg>',
        shield: '<svg viewBox="0 0 22 26" width="20" height="24" aria-hidden="true"><path d="M11 1l9 3v8c0 7-4 11-9 13C6 23 2 19 2 12V4z" fill="#fff" stroke="#C9A227" stroke-width="1.4"/><path d="M11 5v16M5 11h12" stroke="#E8112D" stroke-width="3"/></svg>',
        cross: '<svg viewBox="0 0 22 26" width="18" height="22" aria-hidden="true"><path d="M11 1v24M4 8h14" stroke="#C9A227" stroke-width="3" stroke-linecap="round"/><path d="M2 22q4-3 9 0t9 0" stroke="#5CC8F2" stroke-width="2" fill="none"/></svg>',
        heart: '<svg viewBox="0 0 24 22" width="22" height="20" aria-hidden="true"><path d="M12 21C5 15 1 11 1 6.5A5.5 5.5 0 0 1 12 4a5.5 5.5 0 0 1 11 2.5C23 11 19 15 12 21z" fill="#E0245E"/></svg>',
        balloon: '<svg viewBox="0 0 22 30" width="18" height="25" aria-hidden="true"><ellipse cx="11" cy="10" rx="9" ry="10" fill="#FFB400"/><path d="M11 20l-2 3h4z" fill="#E09A00"/><path d="M11 23q-3 3 0 6" stroke="#888" fill="none"/><ellipse cx="7.5" cy="6.5" rx="2" ry="3" fill="#fff" opacity=".5"/></svg>',
        tulip: '<svg viewBox="0 0 22 28" width="19" height="24" aria-hidden="true"><path d="M11 13v14" stroke="#2E7D32" stroke-width="2"/><path d="M11 22q-6-1-7-7 5 1 7 5" fill="#4C9A2A"/><path d="M4 3l3.5 4L11 2l3.5 5L18 3v6a7 7 0 0 1-14 0z" fill="#E8457A"/></svg>'
    };

    /* falling (or rising) things */
    var FALL = {
        snow: {count: 26, chars: ['❄', '❅', '❆', '•'], colors: ['#ffffff', '#dfe9f7', '#cfe0f5'], spin: true},
        petals: {count: 18, shape: 'petal', colors: ['#F6C1D1', '#F2A7BE', '#FBD9E3', '#ffffff']},
        leaves: {count: 14, shape: 'leaf', colors: ['#D9822B', '#C0581E', '#E3A43B', '#9E4A1C']},
        hearts: {count: 16, chars: ['♥'], colors: ['#E0245E', '#F25C7A', '#B42B5E', '#FF8FAB']},
        confetti: {count: 30, shape: 'confetti', colors: ['#E8112D', '#ffffff', '#E8112D', '#F3F4F8']},
        balloons: {count: 12, shape: 'balloon', rise: true, colors: ['#FFB400', '#E8457A', '#5CC8F2', '#7BE495', '#B48CF2']}
    };

    /* the holidays; the first one that matches a date wins */
    var HOLIDAYS = [
        {id: 'april9', label: '9 აპრილი', solemn: true, badge: 'mourning',
            when: function (d) { return on(d, 4, 9); },
            greet: function () { return '9 აპრილი — ეროვნული ერთიანობის დღე. ვიხსენებთ.'; }},
        {id: 'may9', label: '9 მაისი', solemn: true, badge: 'carnation',
            when: function (d) { return on(d, 5, 9); },
            greet: function () { return '9 მაისი — ფაშიზმზე გამარჯვების დღე. ვიხსენებთ.'; }},
        {id: 'newyear', label: 'ახალი წელი', badge: 'hat', garland: true, fall: 'snow', sample: 'გილოცავთ ახალ წელს!',
            when: function (d) { return between(d, 12, 18, 1, 14); },
            greet: function (d) {
                if (between(d, 12, 31, 1, 2)) { return 'გილოცავთ ახალ წელს!'; }
                if (between(d, 1, 6, 1, 7)) { return 'გილოცავთ შობას!'; }
                if (between(d, 1, 13, 1, 14)) { return 'გილოცავთ ძველით ახალ წელს!'; }
                return '';
            }},
        {id: 'epiphany', label: 'ნათლისღება', badge: 'cross', sample: 'გილოცავთ ნათლისღებას!',
            when: function (d) { return between(d, 1, 18, 1, 19); },
            greet: function (d) { return on(d, 1, 19) ? 'გილოცავთ ნათლისღებას!' : ''; }},
        {id: 'easter', label: 'აღდგომა', badge: 'eggs', sample: 'ქრისტე აღდგა!',
            when: function (d) { var o = easterOffset(d); return o >= -3 && o <= 3; },
            greet: function (d) { var o = easterOffset(d); return o === 0 || o === 1 ? 'ქრისტე აღდგა!' : ''; }},
        {id: 'love', label: 'სიყვარულის დღე', badge: 'heart', fall: 'hearts', sample: 'გილოცავთ სიყვარულის დღეს!',
            when: function (d) { return on(d, 2, 14) || on(d, 4, 15); },
            greet: function () { return 'გილოცავთ სიყვარულის დღეს!'; }},
        {id: 'spring', label: 'გაზაფხული', badge: 'tulip', fall: 'petals', sample: 'გილოცავთ 8 მარტს!',
            when: function (d) { return between(d, 3, 1, 3, 10); },
            greet: function (d) {
                if (on(d, 3, 3)) { return 'გილოცავთ დედის დღეს!'; }
                if (between(d, 3, 7, 3, 8)) { return 'გილოცავთ 8 მარტს!'; }
                return '';
            }},
        {id: 'andria', label: 'ანდრიაობა', badge: 'saltire', sample: 'გილოცავთ ანდრიაობას!',
            when: function (d) { return on(d, 5, 12); },
            greet: function () { return 'გილოცავთ ანდრიაობას!'; }},
        {id: 'may26', label: '26 მაისი', badge: 'flag', fall: 'confetti', sample: 'გილოცავთ დამოუკიდებლობის დღეს!',
            when: function (d) { return between(d, 5, 25, 5, 27); },
            greet: function (d) { return on(d, 5, 26) ? 'გილოცავთ დამოუკიდებლობის დღეს!' : ''; },
            fallOnlyOn: [5, 26]},
        {id: 'children', label: 'ბავშვთა დაცვის დღე', badge: 'balloon', fall: 'balloons', sample: 'ბავშვთა დაცვის საერთაშორისო დღე',
            when: function (d) { return on(d, 6, 1); },
            greet: function () { return 'ბავშვთა დაცვის საერთაშორისო დღე'; }},
        {id: 'mariamoba', label: 'მარიამობა', badge: 'lily', sample: 'გილოცავთ მარიამობას!',
            when: function (d) { return between(d, 8, 27, 8, 28); },
            greet: function (d) { return on(d, 8, 28) ? 'გილოცავთ მარიამობას!' : ''; }},
        {id: 'autumn', label: 'რთველი', badge: 'grapes', fall: 'leaves', sample: 'გილოცავთ სვეტიცხოვლობას!',
            when: function (d) { return between(d, 9, 25, 10, 20); },
            greet: function (d) { return on(d, 10, 14) ? 'გილოცავთ სვეტიცხოვლობას!' : ''; }},
        {id: 'giorgoba', label: 'გიორგობა', badge: 'shield', sample: 'გილოცავთ გიორგობას!',
            when: function (d) { return on(d, 11, 23); },
            greet: function () { return 'გილოცავთ გიორგობას!'; }}
    ];

    function byId(id) {
        for (var i = 0; i < HOLIDAYS.length; i++) { if (HOLIDAYS[i].id === id) { return HOLIDAYS[i]; } }
        return null;
    }

    function iso(d) {
        var m = d.getMonth() + 1, day = d.getDate();
        return d.getFullYear() + '-' + (m < 10 ? '0' : '') + m + '-' + (day < 10 ? '0' : '') + day;
    }

    function holidayFor(date) {
        var today = iso(date);
        if (TEST && today >= TEST.from && today <= TEST.to) { return byId(TEST.id); }
        for (var i = 0; i < HOLIDAYS.length; i++) {
            if (HOLIDAYS[i].when(date)) { return HOLIDAYS[i]; }
        }
        return null;
    }

    var CSS = [
        '.sz-layer{position:fixed;inset:0;pointer-events:none;overflow:hidden;z-index:900}',
        '.sz-p{position:absolute;top:-6vh;will-change:transform,opacity;animation:sz-fall linear forwards;opacity:.9;text-shadow:0 0 3px rgba(27,36,55,.25);user-select:none}',
        '.sz-p.up{top:auto;bottom:-12vh;animation-name:sz-rise}',
        '.sz-petal{border-radius:70% 0 70% 0;box-shadow:0 1px 2px rgba(0,0,0,.08)}',
        '.sz-leaf{border-radius:0 80% 0 80%}',
        '.sz-confetti{border-radius:1px;box-shadow:0 0 1px rgba(0,0,0,.35)}',
        '.sz-balloon{border-radius:50% 50% 48% 48%/55% 55% 45% 45%;box-shadow:inset -4px -6px 0 rgba(0,0,0,.12)}',
        '.sz-balloon::after{content:"";position:absolute;left:50%;top:100%;width:1px;height:26px;background:rgba(80,80,80,.5)}',
        '@keyframes sz-fall{0%{transform:translate3d(0,0,0) rotate(0)}100%{transform:translate3d(var(--dx),112vh,0) rotate(var(--rot));opacity:.15}}',
        '@keyframes sz-rise{0%{transform:translate3d(0,0,0)}100%{transform:translate3d(var(--dx),-125vh,0);opacity:.6}}',
        '.app-nav{position:relative}',
        '.sz-garland{position:absolute;left:0;right:0;bottom:-7px;height:14px;pointer-events:none;display:flex;justify-content:space-around;z-index:5}',
        '.sz-garland::before{content:"";position:absolute;left:0;right:0;top:2px;height:2px;background:rgba(255,255,255,.18)}',
        '.sz-garland i{width:8px;height:11px;border-radius:50% 50% 50% 50%/60% 60% 40% 40%;display:block;animation:sz-tw 2.4s ease-in-out infinite;box-shadow:0 0 6px currentColor;background:currentColor}',
        '.sz-garland i:nth-child(4n+1){color:#F25C7A}.sz-garland i:nth-child(4n+2){color:#FFD166;animation-delay:.6s}',
        '.sz-garland i:nth-child(4n+3){color:#5CC8F2;animation-delay:1.2s}.sz-garland i:nth-child(4n){color:#7BE495;animation-delay:1.8s}',
        '@keyframes sz-tw{0%,100%{opacity:1}50%{opacity:.35}}',
        '.app-nav .brand{position:relative}',
        '.sz-badge{position:absolute;pointer-events:none;line-height:0;filter:drop-shadow(0 1px 2px rgba(0,0,0,.35))}',
        '.sz-badge.hat{top:-9px;left:-8px;transform:rotate(-24deg)}',
        '.sz-badge.side{bottom:-7px;right:-14px;transform:rotate(6deg)}',
        '.sz-greet{position:absolute;left:50%;top:100%;transform:translate(-50%,10px);background:#fff;color:#1B2437;border-radius:999px;padding:6px 16px;font-size:14px;font-weight:600;box-shadow:0 6px 20px rgba(27,36,55,.15);white-space:nowrap;pointer-events:none;z-index:6;animation:sz-in 7s ease forwards}',
        '.sz-greet.solemn{background:#1B2437;color:#fff;font-weight:500}',
        '@keyframes sz-in{0%{opacity:0;transform:translate(-50%,0)}8%,85%{opacity:1;transform:translate(-50%,10px)}100%{opacity:0;transform:translate(-50%,10px)}}',
        '@media print{.sz-layer,.sz-garland,.sz-badge,.sz-greet{display:none!important}}',
        '@media (max-width:700px){.sz-greet{white-space:normal;width:86vw;text-align:center}}',
        '@media (prefers-reduced-motion:reduce){.sz-garland i{animation:none}.sz-p{display:none}.sz-greet{animation:none;opacity:1}}'
    ].join('\n');

    function el(tag, cls, parent) {
        var node = document.createElement(tag);
        if (cls) { node.className = cls; }
        if (parent) { parent.appendChild(node); }
        return node;
    }

    function rand(min, max) { return min + Math.random() * (max - min); }

    function fall(spec) {
        if (!spec || (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)) { return; }
        var layer = el('div', 'sz-layer', document.body);
        layer.setAttribute('aria-hidden', 'true');
        var longest = 0;
        for (var i = 0; i < spec.count; i++) {
            var p = el('span', 'sz-p' + (spec.rise ? ' up' : ''), layer);
            var duration = rand(4.5, 7), delay = rand(0, 2);
            longest = Math.max(longest, duration + delay);
            p.style.left = rand(0, 100) + 'vw';
            p.style.animationDuration = duration + 's';
            p.style.animationDelay = delay + 's';
            p.style.setProperty('--dx', rand(-12, 12) + 'vw');
            p.style.setProperty('--rot', (spec.spin || spec.shape ? rand(-360, 360) : 0) + 'deg');
            var color = spec.colors[i % spec.colors.length];
            if (spec.shape) {
                var w = {petal: rand(8, 14), leaf: rand(9, 14), confetti: rand(6, 10), balloon: rand(22, 32)}[spec.shape];
                var h = {petal: w * 0.8, leaf: w * 1.1, confetti: w * 0.45, balloon: w * 1.2}[spec.shape];
                p.className += ' sz-' + spec.shape;
                p.style.width = w + 'px';
                p.style.height = h + 'px';
                p.style.background = color;
            } else {
                p.textContent = spec.chars[i % spec.chars.length];
                p.style.fontSize = rand(10, 20) + 'px';
                p.style.color = color;
            }
        }
        setTimeout(function () { layer.remove(); }, (longest + 0.5) * 1000);   // nothing keeps running
    }

    function decorate(h, today, preview) {
        var nav = document.querySelector('.app-nav');
        var brand = document.querySelector('.app-nav .brand');
        el('style', '', document.head).textContent = CSS;

        if (nav && h.garland) {
            var garland = el('div', 'sz-garland', nav);
            garland.setAttribute('aria-hidden', 'true');
            var lights = Math.max(10, Math.round(window.innerWidth / 70));
            for (var i = 0; i < lights; i++) { el('i', '', garland); }
        }
        if (brand && h.badge) {
            var badge = el('span', 'sz-badge ' + (h.badge === 'hat' ? 'hat' : 'side'), brand);
            badge.innerHTML = BADGES[h.badge];
        }

        var text = h.greet(today) || (preview ? (h.sample || '') : '');
        if (nav && text) {
            var shown = 'medf-greet-' + iso(today);
            if (preview || !store(true, shown)) {
                var g = el('div', 'sz-greet' + (h.solemn ? ' solemn' : ''), nav);
                g.textContent = text;
                setTimeout(function () { g.remove(); }, 7200);
                store(false, shown, '1');
            }
        }

        var spec = h.solemn ? null : FALL[h.fall];
        if (spec && h.fallOnlyOn && !preview && !on(today, h.fallOnlyOn[0], h.fallOnlyOn[1])) { spec = null; }
        var last = parseInt(store(true, KEY_LAST) || '0', 10);
        if (spec && (preview || Date.now() - last > FALL_EVERY)) {
            store(false, KEY_LAST, String(Date.now()));
            fall(spec);
        }
        // a little fun: Alt + click on the logo for one more
        if (brand && spec) {
            brand.addEventListener('click', function (e) {
                if (e.altKey) { e.preventDefault(); fall(spec); }
            });
            brand.title = 'Alt + დაწკაპება — ' + h.label;
        }
    }

    function addSwitch(h, off) {
        var divider = document.querySelector('.app-nav .dropdown-menu .dropdown-divider');
        if (!divider) { return; }
        var li = document.createElement('li');
        var a = el('a', 'dropdown-item', li);
        a.href = '#';
        a.textContent = 'სეზონური დეკორაცია (' + h.label + '): ' + (off ? 'გამორთულია' : 'ჩართულია');
        a.addEventListener('click', function (e) {
            e.preventDefault();
            store(false, KEY_OFF, off ? null : '1');
            window.location.reload();
        });
        divider.parentNode.parentNode.insertBefore(li, divider.parentNode);
    }

    function start() {
        var today = new Date();
        var forced = (window.location.search.match(/[?&]season=(\w+)/) || [])[1];
        var h = byId(forced) || holidayFor(today);
        if (!h) { return; }
        var off = !byId(forced) && store(true, KEY_OFF) === '1';
        addSwitch(h, off);
        if (!off) { decorate(h, today, !!byId(forced)); }
    }

    window.medfSeason = {holidayFor: holidayFor, orthodoxEaster: orthodoxEaster, ids: HOLIDAYS.map(function (x) { return x.id; })};
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
