/**
 * Common sidebar logic — toggle, role-based nav visibility, logout.
 * Include on every page that uses the sidebar component.
 *
 * Exposes window.sidebarReady (Promise) that resolves with { role, profile }
 * so page-specific scripts can await user data.
 */
(function () {
    'use strict';

    var url = window.__SUPABASE_URL__;
    var key = window.__SUPABASE_ANON_KEY__;
    if (!url || !key || !window.supabase) return;

    var sb = window.supabase.createClient(url, key);
    window._supabase = sb;

    // ---- Collapse / expand ----
    var toggle = document.getElementById('sidebar-toggle');
    var sidebar = document.getElementById('dashboard-sidebar');
    if (toggle && sidebar) {
        var collapsed = localStorage.getItem('dashboard-sidebar-collapsed') === '1';
        if (collapsed) sidebar.classList.add('sidebar-collapsed');
        toggle.addEventListener('click', function () {
            sidebar.classList.toggle('sidebar-collapsed');
            localStorage.setItem(
                'dashboard-sidebar-collapsed',
                sidebar.classList.contains('sidebar-collapsed') ? '1' : '0'
            );
        });
    }

    // ---- Auth + role-based nav ----
    var _resolve;
    window.sidebarReady = new Promise(function (resolve) { _resolve = resolve; });

    sb.auth.getSession().then(function (r) {
        if (!r.data.session) {
            window.location.replace('/login');
            return;
        }
        var accessToken = r.data.session.access_token;
        window.__ACCESS_TOKEN__ = accessToken;

        fetch('/api/org/me', {
            headers: { 'Authorization': 'Bearer ' + accessToken }
        })
            .then(function (res) { return res.ok ? res.json() : null; })
            .then(function (data) {
                var profile = data && data.profile ? data.profile : null;
                var role = profile && profile.role ? String(profile.role).toLowerCase() : '';
                var isAdmin = role === 'admin' || role === 'superadmin';

                // Avatar initials
                var avatarEl = document.getElementById('sidebar-avatar');
                if (avatarEl && profile) {
                    var name = (profile.name || profile.email || '').trim();
                    if (name.length >= 2) {
                        avatarEl.textContent = (
                            name[0] +
                            (name.indexOf(' ') >= 0
                                ? name[name.indexOf(' ') + 1]
                                : name[1] || '')
                        ).toUpperCase();
                    } else if (name.length === 1) {
                        avatarEl.textContent = name[0].toUpperCase();
                    }
                }

                // Org name (if there is an element for it on the page)
                var orgNameEl = document.getElementById('org-name');
                var orgName = data && data.org && data.org.name ? String(data.org.name) : '';
                if (orgNameEl) orgNameEl.textContent = orgName || 'PropMark';

                // Role-based visibility
                show('nav-users', isAdmin);
                show('nav-explore', isAdmin);
                show('nav-daynight', isAdmin);
                show('nav-floorplans', isAdmin);
                show('nav-orgs', role === 'superadmin');
                show('sidebar-upload-wrap', isAdmin);

                // CRM: visible for admin/superadmin — page-specific scripts can
                // refine this (e.g. also show for users who own projects).
                show('nav-crm', isAdmin);

                _resolve({
                    role: role,
                    profile: profile,
                    org: data && data.org ? data.org : null,
                    accessToken: accessToken,
                    isAdmin: isAdmin
                });
            })
            .catch(function () {
                _resolve({ role: '', profile: null, org: null, accessToken: accessToken, isAdmin: false });
            });

        // Change password link — prevent scroll on pages without the modal
        var changePwLink = document.getElementById('nav-change-password');
        if (changePwLink) {
            changePwLink.addEventListener('click', function (e) {
                e.preventDefault();
            });
        }

        // Logout handler
        var logoutEl = document.getElementById('nav-logout');
        if (logoutEl) {
            logoutEl.addEventListener('click', function (e) {
                e.preventDefault();
                sb.auth.signOut().then(function () {
                    window.location.href = '/login';
                });
            });
        }
    }).catch(function () {
        window.location.replace('/login');
    });

    function show(id, visible) {
        var el = document.getElementById(id);
        if (el) el.style.display = visible ? '' : 'none';
    }
})();
