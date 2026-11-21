/* Seasonal decorations ("სეზონური დეკორაციები").
 *
 * Runs only in the browser, by the computer's date: the server does nothing
 * extra. Rules, so that nobody is disturbed at work:
 *   - decorations never catch clicks (pointer-events: none) and are not printed;
 *   - falling snow / petals / leaves last at most ~9 seconds and show at most once
 *     every 30 minutes in a browser;
 *   - "reduce motion" in the system settings = no falling, no twinkling;
 *   - user menu -> "სეზონური დეკორაცია" switches everything off in this browser.
 * Preview any season: add ?season=newyear (spring, easter, may26, autumn) to the address.
 */
(function () {
    'use strict';

    var KEY_OFF = 'medf-season-off';
    var KEY_LAST = 'medf-season-fall';
    var FALL_EVERY = 30 * 60 * 1000;

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

    function between(date, m1, d1, m2, d2) {
        var v = (date.getMonth() + 1) * 100 + date.getDate();
        var from = m1 * 100 + d1, to = m2 * 100 + d2;
        return from <= to ? v >= from && v <= to : v >= from || v <= to;   // across New Year
    }

    function seasonFor(date) {
        var easter = orthodoxEaster(date.getFullYear());
        var days = Math.round((new Date(date.getFullYear(), date.getMonth(), date.getDate()) - easter) / 86400000);
        if (between(date, 12, 18, 1, 14)) { return 'newyear'; }
        if (days >= -3 && days <= 3) { return 'easter'; }
        if (between(date, 3, 1, 3, 10)) { return 'spring'; }
        if (between(date, 5, 25, 5, 27)) { return 'may26'; }
        if (between(date, 9, 25, 10, 20)) { return 'autumn'; }
        return null;
    }

    var SEASONS = {
        newyear: {
            label: 'ახალი წელი', sample: 'გილოცავთ ახალ წელს!',
            greet: function (d) {
                if (between(d, 12, 31, 1, 2)) { return 'გილოცავთ ახალ წელს!'; }
                if (between(d, 1, 6, 1, 7)) { return 'გილოცავთ შობას!'; }
                if (between(d, 1, 13, 1, 14)) { return 'გილოცავთ ძველით ახალ წელს!'; }
                return '';
            },
            fall: {count: 26, chars: ['❄', '❅', '❆', '•'], colors: ['#ffffff', '#dfe9f7', '#cfe0f5'], spin: true}
        },
        spring: {
            label: 'გაზაფხული', sample: 'გილოცავთ 8 მარტს!',
            greet: function (d) { return between(d, 3, 7, 3, 8) ? 'გილოცავთ 8 მარტს!' : ''; },
            fall: {count: 18, petals: true, colors: ['#F6C1D1', '#F2A7BE', '#FBD9E3', '#ffffff']}
        },
        easter: {
            label: 'აღდგომა', sample: 'ქრისტე აღდგა!',
            greet: function (d) { return Math.round((d - orthodoxEaster(d.getFullYear())) / 86400000) === 0 ? 'ქრისტე აღდგა!' : ''; },
            fall: null
        },
        may26: {
            label: '26 მაისი', sample: 'გილოცავთ დამოუკიდებლობის დღეს!',
            greet: function (d) { return between(d, 5, 26, 5, 26) ? 'გილოცავთ დამოუკიდებლობის დღეს!' : ''; },
            fall: null
        },
        autumn: {
            label: 'რთველი', sample: '',
            greet: function () { return ''; },
            fall: {count: 14, petals: true, leaf: true, colors: ['#D9822B', '#C0581E', '#E3A43B', '#9E4A1C']}
        }
    };

    var CSS = [
        '.sz-layer{position:fixed;inset:0;pointer-events:none;overflow:hidden;z-index:900}',
        '.sz-p{position:absolute;top:-6vh;will-change:transform,opacity;animation:sz-fall linear forwards;opacity:.9;text-shadow:0 0 3px rgba(27,36,55,.25);user-select:none}',
        '.sz-petal{border-radius:70% 0 70% 0;box-shadow:0 1px 2px rgba(0,0,0,.08)}',
        '.sz-leaf{border-radius:0 80% 0 80%}',
        '@keyframes sz-fall{0%{transform:translate3d(0,0,0) rotate(0)}100%{transform:translate3d(var(--dx),112vh,0) rotate(var(--rot));opacity:.15}}',
        /* garland of lights along the bottom of the menu bar */
        '.app-nav{position:relative}',
        '.sz-garland{position:absolute;left:0;right:0;bottom:-7px;height:14px;pointer-events:none;display:flex;justify-content:space-around;z-index:5}',
        '.sz-garland::before{content:"";position:absolute;left:0;right:0;top:2px;height:2px;background:rgba(255,255,255,.18)}',
        '.sz-garland i{width:8px;height:11px;border-radius:50% 50% 50% 50%/60% 60% 40% 40%;display:block;animation:sz-tw 2.4s ease-in-out infinite;box-shadow:0 0 6px currentColor;background:currentColor}',
        '.sz-garland i:nth-child(4n+1){color:#F25C7A}.sz-garland i:nth-child(4n+2){color:#FFD166;animation-delay:.6s}',
        '.sz-garland i:nth-child(4n+3){color:#5CC8F2;animation-delay:1.2s}.sz-garland i:nth-child(4n){color:#7BE495;animation-delay:1.8s}',
        '@keyframes sz-tw{0%,100%{opacity:1}50%{opacity:.35}}',
        /* a small hat / egg / flag next to the logo */
        '.app-nav .brand{position:relative}',
        '.sz-badge{position:absolute;pointer-events:none;line-height:1}',
        '.sz-hat{top:-9px;left:-8px;width:26px;height:22px;transform:rotate(-24deg)}',
        '.sz-eggs{bottom:-6px;right:-14px;display:flex;gap:2px}',
        '.sz-eggs i{display:block;width:11px;height:14px;border-radius:50% 50% 50% 50%/60% 60% 40% 40%;background:#C8102E;box-shadow:inset -2px -2px 0 rgba(0,0,0,.15)}',
        '.sz-eggs i:nth-child(2){background:#B42B5E;margin-top:3px}',
        '.sz-flag{bottom:-7px;right:-16px;width:30px;height:20px;box-shadow:0 1px 4px rgba(0,0,0,.35);border-radius:2px;overflow:hidden;transform:rotate(6deg)}',
        '.sz-greet{position:absolute;left:50%;top:100%;transform:translate(-50%,10px);background:#fff;color:#1B2437;border-radius:999px;padding:6px 16px;font-size:14px;font-weight:600;box-shadow:0 6px 20px rgba(27,36,55,.15);white-space:nowrap;pointer-events:none;z-index:6;animation:sz-in 7s ease forwards}',
        '@keyframes sz-in{0%{opacity:0;transform:translate(-50%,0)}8%,85%{opacity:1;transform:translate(-50%,10px)}100%{opacity:0;transform:translate(-50%,10px)}}',
        '@media print{.sz-layer,.sz-garland,.sz-badge,.sz-flag,.sz-greet{display:none!important}}',
        '@media (prefers-reduced-motion:reduce){.sz-garland i{animation:none}.sz-p{display:none}}'
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
            var p = el('span', 'sz-p', layer);
            var size = spec.petals ? rand(8, 14) : rand(10, 20);
            var duration = rand(4.5, 7), delay = rand(0, 2);
            longest = Math.max(longest, duration + delay);
            p.style.left = rand(0, 100) + 'vw';
            p.style.animationDuration = duration + 's';
            p.style.animationDelay = delay + 's';
            p.style.setProperty('--dx', rand(-12, 12) + 'vw');
            p.style.setProperty('--rot', (spec.spin || spec.petals ? rand(-360, 360) : 0) + 'deg');
            var color = spec.colors[i % spec.colors.length];
            if (spec.petals) {
                p.className += spec.leaf ? ' sz-leaf' : ' sz-petal';
                p.style.width = size + 'px';
                p.style.height = size * (spec.leaf ? 1.1 : 0.8) + 'px';
                p.style.background = color;
            } else {
                p.textContent = spec.chars[i % spec.chars.length];
                p.style.fontSize = size + 'px';
                p.style.color = color;
            }
        }
        setTimeout(function () { layer.remove(); }, (longest + 0.5) * 1000);   // nothing keeps running
    }

    function decorate(name, today, preview) {
        var season = SEASONS[name];
        var nav = document.querySelector('.app-nav');
        var brand = document.querySelector('.app-nav .brand');
        var style = el('style', '', document.head);
        style.textContent = CSS;

        if (nav && name === 'newyear') {
            var garland = el('div', 'sz-garland', nav);
            garland.setAttribute('aria-hidden', 'true');
            var lights = Math.max(10, Math.round(window.innerWidth / 70));
            for (var i = 0; i < lights; i++) { el('i', '', garland); }
            if (brand) {
                var hat = el('span', 'sz-badge sz-hat', brand);
                hat.innerHTML = '<svg viewBox="0 0 26 22" width="26" height="22" aria-hidden="true"><path d="M2 18 C6 6 14 1 22 6 L18 10 C14 8 10 12 9 18 Z" fill="#D7263D"/><rect x="0" y="16" width="14" height="5" rx="2.5" fill="#fff"/><circle cx="22" cy="6" r="3" fill="#fff"/></svg>';
            }
        }
        if (brand && name === 'easter') {
            var eggs = el('span', 'sz-badge sz-eggs', brand);
            el('i', '', eggs); el('i', '', eggs);
        }
        if (brand && name === 'may26') {
            var flag = el('span', 'sz-badge sz-flag', brand);
            // the five-cross flag of Georgia
            flag.innerHTML = '<svg viewBox="0 0 300 200" width="30" height="20" aria-hidden="true"><rect width="300" height="200" fill="#fff"/>' +
                '<rect x="130" width="40" height="200" fill="#E8112D"/><rect y="80" width="300" height="40" fill="#E8112D"/>' +
                '<g fill="#E8112D"><path id="szc" d="M58 23h8v15h15v8H66v15h-8V46H43v-8h15z"/>' +
                '<use href="#szc" x="176"/><use href="#szc" y="115"/><use href="#szc" x="176" y="115"/></g></svg>';
        }

        var text = season.greet(today) || (preview ? season.sample : '');
        if (nav && text) {
            var shown = 'medf-greet-' + today.toDateString();
            if (preview || !store(true, shown)) {
                var g = el('div', 'sz-greet', nav);
                g.textContent = text;
                setTimeout(function () { g.remove(); }, 7200);
                store(false, shown, '1');
            }
        }

        var last = parseInt(store(true, KEY_LAST) || '0', 10);
        if (preview || Date.now() - last > FALL_EVERY) {
            store(false, KEY_LAST, String(Date.now()));
            fall(season.fall);
        }
        // a little fun: click the logo for one more snowfall / petals
        if (brand && season.fall) {
            brand.addEventListener('click', function (e) {
                if (e.altKey) { e.preventDefault(); fall(season.fall); }
            });
            brand.title = 'Alt + დაწკაპება — ' + season.label;
        }
    }

    function addSwitch(name, off) {
        var divider = document.querySelector('.app-nav .dropdown-menu .dropdown-divider');
        if (!divider) { return; }
        var li = document.createElement('li');
        var a = el('a', 'dropdown-item', li);
        a.href = '#';
        a.textContent = 'სეზონური დეკორაცია (' + SEASONS[name].label + '): ' + (off ? 'გამორთულია' : 'ჩართულია');
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
        var name = SEASONS[forced] ? forced : seasonFor(today);
        if (!name) { return; }
        var off = !forced && store(true, KEY_OFF) === '1';
        addSwitch(name, off);
        if (!off) { decorate(name, today, !!forced); }
    }

    window.medfSeason = {seasonFor: seasonFor, orthodoxEaster: orthodoxEaster};   // for tests
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
