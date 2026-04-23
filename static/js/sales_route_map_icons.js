(function () {
    'use strict';

    var ICONS = [
        {
            key: 'main-star',
            group: 'main',
            label: 'Star',
            svg: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.8l2.9 5.86 6.47.94-4.68 4.56 1.1 6.44L12 17.6l-5.79 3.04 1.1-6.44-4.68-4.56 6.47-.94L12 2.8z"/></svg>'
        },
        {
            key: 'main-home',
            group: 'main',
            label: 'Home',
            svg: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.2l9 7.4v9.2h-6.2v-5.6H9.2v5.6H3V10.6l9-7.4z"/></svg>'
        },
        {
            key: 'main-flag',
            group: 'main',
            label: 'Flag',
            svg: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 2.5v19h2.2v-6.1h10.1l-1.9-3.5 1.9-3.5H8.2V2.5H6z"/></svg>'
        },
        {
            key: 'main-crown',
            group: 'main',
            label: 'Crown',
            svg: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 8.6l4.4 3.3L12 6.2l4.6 5.7L21 8.6l-2.1 10H5.1L3 8.6z"/></svg>'
        },
        {
            key: 'plot-pin',
            group: 'normal',
            label: 'Pin',
            svg: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.2c-3.6 0-6.6 2.8-6.6 6.3 0 4.7 6.6 13.3 6.6 13.3s6.6-8.6 6.6-13.3c0-3.5-3-6.3-6.6-6.3zm0 9a2.7 2.7 0 1 1 0-5.4 2.7 2.7 0 0 1 0 5.4z"/></svg>'
        },
        {
            key: 'plot-dot',
            group: 'normal',
            label: 'Dot',
            svg: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="6.8"/></svg>'
        },
        {
            key: 'plot-square',
            group: 'normal',
            label: 'Square',
            svg: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1.8"/></svg>'
        },
        {
            key: 'plot-gate',
            group: 'normal',
            label: 'Gate',
            svg: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20V9.5h3.2V20H4zm12.8 0V9.5H20V20h-3.2zM8.8 20v-8h6.4v8H8.8zm-1-10.3L12 5.2l4.2 4.5H7.8z"/></svg>'
        },
        {
            key: 'plot-tree',
            group: 'normal',
            label: 'Tree',
            svg: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.2l4.6 6h-2.5l3.1 4h-3.1l2.3 3.1H7.6l2.3-3.1H6.8l3.1-4H7.4l4.6-6zm-1.3 13.1h2.6v4.5h-2.6v-4.5z"/></svg>'
        },
        {
            key: 'plot-office',
            group: 'normal',
            label: 'Office',
            svg: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 20V5.2L13 3v17H4.5zm4.2-12h1.8v1.8H8.7V8zm0 3.4h1.8v1.8H8.7v-1.8zm0 3.4h1.8v1.8H8.7v-1.8zM14.3 20v-9H20V20h-5.7zm2-5.9h1.6v1.6h-1.6v-1.6z"/></svg>'
        }
    ];

    var ICONS_BY_KEY = {};
    ICONS.forEach(function (icon) {
        ICONS_BY_KEY[icon.key] = icon;
    });

    function normalizeKey(value) {
        return String(value || '').trim().toLowerCase();
    }

    function defaultForType(markerType) {
        return String(markerType || '').trim().toLowerCase() === 'main' ? 'main-star' : 'plot-pin';
    }

    function getIcon(key) {
        var normalized = normalizeKey(key);
        return ICONS_BY_KEY[normalized] || null;
    }

    function renderSvg(key, markerType) {
        var icon = getIcon(key) || getIcon(defaultForType(markerType));
        return icon ? icon.svg : '';
    }

    function byGroup(group) {
        var normalized = normalizeKey(group);
        return ICONS.filter(function (icon) { return normalizeKey(icon.group) === normalized; });
    }

    window.salesRouteMapIcons = {
        list: ICONS,
        get: getIcon,
        byGroup: byGroup,
        normalizeKey: normalizeKey,
        defaultForType: defaultForType,
        renderSvg: renderSvg,
        isAllowed: function (key) { return !!getIcon(key); }
    };
})();
