(function () {
    function clamp(n, a, b) { return Math.max(a, Math.min(b, n)); }

    function normalizeHex(hex) {
        var v = String(hex || '').trim();
        if (!v) return '';
        if (v[0] !== '#') v = '#' + v;
        if (v.length === 4) {
            v = '#' + v[1] + v[1] + v[2] + v[2] + v[3] + v[3];
        }
        if (!/^#[0-9a-fA-F]{6}$/.test(v)) return '';
        return v.toLowerCase();
    }

    function hexToRgb(hex) {
        var v = normalizeHex(hex);
        if (!v) return null;
        var n = parseInt(v.slice(1), 16);
        return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
    }

    function rgbToHex(rgb) {
        function h(x) { var s = clamp(Math.round(x), 0, 255).toString(16); return s.length === 1 ? '0' + s : s; }
        return '#' + h(rgb.r) + h(rgb.g) + h(rgb.b);
    }

    function lighten(hex, amount) {
        var rgb = hexToRgb(hex);
        if (!rgb) return '';
        var a = clamp(Number(amount) || 0, 0, 1);
        return rgbToHex({
            r: rgb.r + (255 - rgb.r) * a,
            g: rgb.g + (255 - rgb.g) * a,
            b: rgb.b + (255 - rgb.b) * a
        });
    }

    async function getAuthHeaders() {
        if (typeof window.getAuthHeaders === 'function') {
            try { return await window.getAuthHeaders(); } catch (e) { }
        }
        var sb = window._supabase;
        if (sb && sb.auth && typeof sb.auth.getSession === 'function') {
            try {
                var r = await sb.auth.getSession();
                var t = r && r.data && r.data.session ? r.data.session.access_token : null;
                return t ? { Authorization: 'Bearer ' + t } : {};
            } catch (e) { }
        }
        return {};
    }

    async function applyOrgTheme() {
        try {
            var headers = await getAuthHeaders();
            var auth = headers && (headers.Authorization || headers.authorization);
            if (!auth) return;

            var r = await fetch('/api/org/me', { headers: headers });
            if (!r.ok) return;
            var data = null;
            try { data = await r.json(); } catch (e) { data = null; }
            var accent = '';
            if (data && data.org && data.org.accent_color) accent = String(data.org.accent_color);
            accent = normalizeHex(accent);
            if (!accent) return;

            var root = document.documentElement;
            var current = (getComputedStyle(root).getPropertyValue('--color-accent') || '').trim();
            var currentNorm = normalizeHex(current);
            if (currentNorm && currentNorm === accent) {
                try {
                    document.cookie = 'org_accent=' + encodeURIComponent(accent) + '; path=/; max-age=86400; SameSite=Lax';
                } catch (e) { }
                return;
            }

            var rgb = hexToRgb(accent);
            if (!rgb) return;

            // Common variables used across templates.
            root.style.setProperty('--color-accent', accent);
            root.style.setProperty('--color-accent-hover', lighten(accent, 0.08) || accent);
            root.style.setProperty('--color-accent-dim', 'rgba(' + rgb.r + ', ' + rgb.g + ', ' + rgb.b + ', 0.3)');
            root.style.setProperty('--accent', accent);
            root.style.setProperty('--accent-2', lighten(accent, 0.18) || accent);
            // Persist in cookie so next full page load (login/dashboard) can inject theme in HTML and avoid default-color flash.
            try {
                document.cookie = 'org_accent=' + encodeURIComponent(accent) + '; path=/; max-age=86400; SameSite=Lax';
            } catch (e) { }
        } catch (e) { }
    }

    window.applyOrgTheme = applyOrgTheme;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { applyOrgTheme(); });
    } else {
        applyOrgTheme();
    }
})();
