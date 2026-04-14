/**
 * Common sidebar logic — toggle, role-based nav visibility, logout.
 * Include on every page that uses the sidebar component.
 *
 * Exposes window.sidebarReady (Promise) that resolves with { role, profile }
 * so page-specific scripts can await user data.
 */
(function () {
    'use strict';

    var CACHE_KEY = 'sidebar_cache';

    var url = window.__SUPABASE_URL__;
    var key = window.__SUPABASE_ANON_KEY__;
    if (!url || !key || !window.supabase) return;

    var sb = window.supabase.createClient(url, key);
    window._supabase = sb;

    // ---- Active link highlight based on current URL ----
    var currentPath = window.location.pathname.replace(/\/+$/, '') || '/';
    document.querySelectorAll('.sidebar-link[data-page]').forEach(function (link) {
        if (link.getAttribute('data-coming-soon') === '1') {
            link.classList.remove('active');
            return;
        }
        var href = (link.getAttribute('href') || '').replace(/\/+$/, '') || '/';
        var isSalesToolsAlias = (href === '/floorplans' && currentPath === '/daynight');
        if (currentPath === href || currentPath.indexOf(href + '/') === 0 || isSalesToolsAlias) {
            link.classList.add('active');
        } else {
            link.classList.remove('active');
        }
    });

    document.querySelectorAll('.sidebar-link[data-coming-soon="1"]').forEach(function (link) {
        link.addEventListener('click', function (e) {
            e.preventDefault();
        });
    });

    // ---- Collapse / expand (collapsed by default) ----
    var toggle = document.getElementById('sidebar-toggle');
    var sidebar = document.getElementById('dashboard-sidebar');
    if (toggle && sidebar) {
        var expanded = localStorage.getItem('dashboard-sidebar-collapsed') === '0';
        if (!expanded) sidebar.classList.add('sidebar-collapsed');
        toggle.addEventListener('click', function () {
            sidebar.classList.toggle('sidebar-collapsed');
            localStorage.setItem(
                'dashboard-sidebar-collapsed',
                sidebar.classList.contains('sidebar-collapsed') ? '1' : '0'
            );
        });
    }

    // ---- Helper: apply cached sidebar state instantly ----
    function applySidebarState(state) {
        // Avatar initials
        var avatarEl = document.getElementById('sidebar-avatar');
        if (avatarEl && state.avatarText) {
            avatarEl.textContent = state.avatarText;
        }
        // Org name
        var orgNameEl = document.getElementById('org-name');
        if (orgNameEl) orgNameEl.textContent = state.orgName || 'PropMark';
        // User name
        var userNameEl = document.getElementById('sidebar-user-name');
        if (userNameEl) userNameEl.textContent = state.userName || '';
        // Role-based visibility
        show('nav-explore', state.isAdmin);
        show('nav-floorplans', state.isAdmin);
        show('nav-fullview', state.isAdmin);
        show('nav-orgs', state.role === 'superadmin');
        show('sidebar-upload-wrap', state.isAdmin);
        show('dropdown-add-user', state.isAdmin);
        show('dropdown-user-mgmt', state.isAdmin);
        show('dropdown-client-mgmt', !state.isAdmin && !!state.isClientAdmin);
        show('nav-crm', state.showCrm);
    }

    // ---- Restore cached sidebar immediately (no flash) ----
    var cached = null;
    try { cached = JSON.parse(sessionStorage.getItem(CACHE_KEY)); } catch (e) { /* ignore */ }
    if (cached) {
        applySidebarState(cached);
    }

    // ---- Auth + role-based nav ----
    var _resolve;
    window.sidebarReady = new Promise(function (resolve) { _resolve = resolve; });

    sb.auth.getSession().then(function (r) {
        if (!r.data.session) {
            sessionStorage.removeItem(CACHE_KEY);
            window.location.replace('/login');
            return;
        }
        var accessToken = r.data.session.access_token;
        window.__ACCESS_TOKEN__ = accessToken;

        fetch('/api/org/me', {
            headers: { 'Authorization': 'Bearer ' + accessToken }
        })
            .then(function (res) {
                if (res.status === 401 || res.status === 403) {
                    sessionStorage.removeItem(CACHE_KEY);
                    sb.auth.signOut().finally(function () { window.location.replace('/login'); });
                    return null;
                }
                return res.ok ? res.json() : null;
            })
            .then(function (data) {
                if (!data) return;
                var profile = data && data.profile ? data.profile : null;
                var role = profile && profile.role ? String(profile.role).toLowerCase() : '';
                var isAdmin = role === 'admin' || role === 'superadmin';

                // Build avatar text
                var avatarText = '';
                if (profile) {
                    var name = (profile.name || profile.email || '').trim();
                    if (name.length >= 2) {
                        avatarText = (
                            name[0] +
                            (name.indexOf(' ') >= 0
                                ? name[name.indexOf(' ') + 1]
                                : name[1] || '')
                        ).toUpperCase();
                    } else if (name.length === 1) {
                        avatarText = name[0].toUpperCase();
                    }
                }

                var orgName = data && data.org && data.org.name ? String(data.org.name) : '';
                var userName = profile ? (profile.name || profile.email || '') : '';

                // CRM: visible for admin/superadmin immediately.
                // For regular users, check if they have client access to any plot.
                if (isAdmin) {
                    var state = {
                        role: role, isAdmin: isAdmin, showCrm: true,
                        avatarText: avatarText, orgName: orgName, userName: userName
                    };
                    applySidebarState(state);
                    try { sessionStorage.setItem(CACHE_KEY, JSON.stringify(state)); } catch (e) { /* ignore */ }
                    finishSidebar();
                } else {
                    fetch('/api/crm/me', {
                        headers: { 'Authorization': 'Bearer ' + accessToken }
                    })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (crmData) {
                        var hasCrm = crmData && crmData.has_crm_access;
                        var isClientAdmin = !!(crmData && crmData.is_client_admin);
                        var state = {
                            role: role, isAdmin: isAdmin, showCrm: hasCrm,
                            isClientAdmin: isClientAdmin,
                            avatarText: avatarText, orgName: orgName, userName: userName
                        };
                        applySidebarState(state);
                        try { sessionStorage.setItem(CACHE_KEY, JSON.stringify(state)); } catch (e) { /* ignore */ }
                        finishSidebar();
                    })
                    .catch(function () {
                        var state = {
                            role: role, isAdmin: isAdmin, showCrm: false,
                            isClientAdmin: false,
                            avatarText: avatarText, orgName: orgName, userName: userName
                        };
                        applySidebarState(state);
                        try { sessionStorage.setItem(CACHE_KEY, JSON.stringify(state)); } catch (e) { /* ignore */ }
                        finishSidebar();
                    });
                }

                function finishSidebar() {
                    _resolve({
                        role: role,
                        profile: profile,
                        org: data && data.org ? data.org : null,
                        accessToken: accessToken,
                        isAdmin: isAdmin
                    });
                }
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
                sessionStorage.removeItem(CACHE_KEY);
                sb.auth.signOut().then(function () {
                    window.location.href = '/login';
                });
            });
        }
    }).catch(function () {
        sb.auth.signOut().finally(function () { window.location.replace('/login'); });
    });

    // ---- User dropdown toggle ----
    var userBtn = document.getElementById('sidebar-user-btn');
    var userDropdown = document.getElementById('sidebar-user-dropdown');
    if (userBtn && userDropdown) {
        userBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            userDropdown.classList.toggle('visible');
        });
        document.addEventListener('click', function (e) {
            if (!userDropdown.contains(e.target) && e.target !== userBtn) {
                userDropdown.classList.remove('visible');
            }
        });
    }

    function show(id, visible) {
        var el = document.getElementById(id);
        if (el) el.style.display = visible ? '' : 'none';
    }
})();
