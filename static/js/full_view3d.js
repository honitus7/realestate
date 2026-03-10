(function () {
    'use strict';

    var config = window.FULL_VIEW_CONFIG || {};
    var WORKSPACE_PANORAMAS = Array.isArray(config.workspacePanoramas) ? config.workspacePanoramas : [];
    var ORG_SLUG = String(config.orgSlug || '').trim();
    var INITIAL_PANORAMA_ID = config.initialPanoramaId != null ? Number(config.initialPanoramaId) : null;

    if (!WORKSPACE_PANORAMAS.length) {
        document.body.innerHTML = '<p style="padding:2rem;text-align:center;">No connected panoramas. Use the dashboard Share Full View link for a folder with multiple 360° views.</p>';
        return;
    }

    var mainId = WORKSPACE_PANORAMAS[0] && WORKSPACE_PANORAMAS[0].id != null ? Number(WORKSPACE_PANORAMAS[0].id) : null;
    var currentPanoramaId = INITIAL_PANORAMA_ID != null && WORKSPACE_PANORAMAS.some(function (p) { return Number(p.id) === INITIAL_PANORAMA_ID; })
        ? INITIAL_PANORAMA_ID
        : mainId;

    var historyStack = [];
    var viewer = null;
    var markersPlugin = null;
    var plots = [];
    var markerMap = new Map();
    var cart = [];

    var loading = document.getElementById('loading');
    var psvContainer = document.getElementById('psv-container');
    var backBtn = document.getElementById('back-btn');
    var panoramaNavShell = document.getElementById('panorama-nav-shell');
    var panoramaNavToggle = document.getElementById('panorama-nav-toggle');
    var panoramaNavToggleIcon = document.getElementById('panorama-nav-toggle-icon');
    var panoramaNavList = document.getElementById('panorama-nav-list');
    var plotDetailsModal = document.getElementById('plot-details-modal');
    var markerModal = document.getElementById('marker-readonly-modal');
    var enterMarkerPanoramaBtn = document.getElementById('enter-marker-panorama');
    var enterPlotPanoramaBtn = document.getElementById('enter-plot-panorama');

    var TRANSITION_MS = 1500;

    function getWorkspacePanoramaById(panoramaId) {
        var id = panoramaId != null ? String(panoramaId) : '';
        for (var i = 0; i < WORKSPACE_PANORAMAS.length; i++) {
            if (String(WORKSPACE_PANORAMAS[i].id) === id) return WORKSPACE_PANORAMAS[i];
        }
        return null;
    }

    function getImageUrl(panoramaId) {
        var p = getWorkspacePanoramaById(panoramaId);
        if (!p || !p.filename) return null;
        return '/uploads/' + encodeURIComponent(p.filename);
    }

    function escapeHtml(str) {
        if (str == null) return '';
        var s = String(str);
        var div = document.createElement('div');
        div.textContent = s;
        return div.innerHTML;
    }

    function updateUrl(panoramaId) {
        if (!window.history || !window.history.replaceState) return;
        var base = window.location.pathname;
        var params = new URLSearchParams();
        params.set('p', String(panoramaId));
        window.history.replaceState({ panoramaId: panoramaId }, '', base + '?' + params.toString());
    }

    function renderPanoramaList() {
        if (!panoramaNavList) return;
        var currentId = String(currentPanoramaId);
        var html = '';
        for (var i = 0; i < WORKSPACE_PANORAMAS.length; i++) {
            var p = WORKSPACE_PANORAMAS[i];
            var id = String(p.id);
            var name = (p.name || 'Panorama #' + id).trim();
            var active = id === currentId ? ' active' : '';
            html += '<button type="button" class="panorama-nav-item' + active + '" data-panorama-id="' + escapeHtml(id) + '">';
            html += '<span class="panorama-nav-item-name">' + escapeHtml(name) + '</span>';
            html += '<span class="panorama-nav-item-meta">' + (id === currentId ? 'Current view' : 'Click to switch') + '</span>';
            html += '</button>';
        }
        panoramaNavList.innerHTML = html;
        panoramaNavList.querySelectorAll('.panorama-nav-item[data-panorama-id]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var targetId = btn.getAttribute('data-panorama-id');
                if (!targetId || targetId === String(currentPanoramaId)) return;
                historyStack.push(String(currentPanoramaId));
                backBtn.hidden = false;
                switchToPanorama(targetId);
            });
        });
    }

    function switchToPanorama(panoramaId, skipHistory) {
        var id = panoramaId != null ? String(panoramaId) : '';
        if (!id || !viewer) return Promise.resolve();
        var url = getImageUrl(id);
        if (!url) return Promise.resolve();

        var opts = { showLoader: true, transition: true, transitionDuration: TRANSITION_MS };
        if (typeof viewer.setPanorama !== 'function') return Promise.resolve();

        return viewer.setPanorama(url, opts).then(function () {
            currentPanoramaId = id;
            if (!skipHistory) updateUrl(id);
            renderPanoramaList();
            loadPlots(id);
            loadMarkers(id);
            var headerName = document.getElementById('header-project-name');
            if (headerName) {
                var p = getWorkspacePanoramaById(id);
                headerName.textContent = p && p.name ? p.name : 'Full View';
            }
        }).catch(function () {});
    }

    function loadPlots(panoramaId) {
        var pid = panoramaId != null ? String(panoramaId) : String(currentPanoramaId);
        if (!pid) return;
        fetch('/api/public/panoramas/' + encodeURIComponent(pid) + '/plots')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (String(currentPanoramaId) !== String(pid)) return;
                plots = Array.isArray(data) ? data : [];
                updateStatusLegend();
                syncAllMarkers();
            })
            .catch(function () {
                if (String(currentPanoramaId) !== String(pid)) return;
                plots = [];
                syncAllMarkers();
            });
    }

    function getCentroid(points) {
        if (!Array.isArray(points) || points.length === 0) return null;
        var x = 0, y = 0;
        for (var i = 0; i < points.length; i++) {
            x += Number(points[i].x) || 0;
            y += Number(points[i].y) || 0;
        }
        return { x: x / points.length, y: y / points.length };
    }

    var statusColors = {
        available: { fill: '#c6d97f', fillOpacity: 0.62, stroke: '#edf3de' },
        on_hold: { fill: '#9ecdad', fillOpacity: 0.6, stroke: '#e3f1e6' },
        sold: { fill: '#e2aba9', fillOpacity: 0.61, stroke: '#f7e8e8' }
    };
    function getStatusKey(s) {
        var t = (s || '').toLowerCase();
        if (t === 'sold') return 'sold';
        if (t === 'reserved' || t === 'on_hold') return 'on_hold';
        return 'available';
    }

    function buildPlotMarkers() {
        var markers = [];
        if (!viewer || !markersPlugin) return markers;
        for (var i = 0; i < plots.length; i++) {
            var plot = plots[i];
            if (!Array.isArray(plot.points) || plot.points.length < 3) continue;
            var key = getStatusKey(plot.status);
            var style = statusColors[key] || statusColors.available;
            markers.push({
                id: 'plot-' + plot.id,
                polygonPx: plot.points.map(function (pt) { return [pt.x, pt.y]; }),
                svgStyle: { fill: style.fill, fillOpacity: style.fillOpacity, stroke: style.stroke, strokeOpacity: 0.94, strokeWidth: 2 }
            });
            var labelLon = plot.label_longitude;
            var labelLat = plot.label_latitude;
            var centroid = getCentroid(plot.points);
            if (centroid && plot.name && viewer.dataHelper) {
                if (typeof labelLon !== 'number' || typeof labelLat !== 'number') {
                    var spherical = viewer.dataHelper.textureCoordsToSphericalCoords(centroid);
                    labelLon = spherical.longitude;
                    labelLat = spherical.latitude;
                }
                var labelClass = key === 'sold' ? 'psv-plot-label--sold' : (key === 'on_hold' ? 'psv-plot-label--hold' : 'psv-plot-label--available');
                markers.push({
                    id: 'plot-label-' + plot.id,
                    longitude: labelLon,
                    latitude: labelLat,
                    html: '<div class="psv-plot-label ' + labelClass + '">' + escapeHtml(plot.name) + '</div>',
                    anchor: 'center center',
                    style: { pointerEvents: 'none' },
                    data: { plotId: plot.id, label: true }
                });
            }
        }
        return markers;
    }

    function buildPoiMarkers() {
        var markers = [];
        markerMap.forEach(function (marker) {
            var html = '<button type="button" class="psv-glass-marker" data-marker-id="' + escapeHtml(marker.id) + '" aria-label="' + escapeHtml(marker.name || 'Marker') + '">';
            html += '<span class="marker-tag">' + escapeHtml(marker.name || 'Marker') + '</span></button>';
            markers.push({
                id: 'poi-' + marker.id,
                longitude: marker.longitude,
                latitude: marker.latitude,
                html: html,
                anchor: 'center bottom',
                data: { markerType: 'poi', markerId: marker.id }
            });
        });
        return markers;
    }

    function syncAllMarkers() {
        if (!markersPlugin) return;
        var list = buildPlotMarkers().concat(buildPoiMarkers());
        markersPlugin.setMarkers(list);
    }

    function updateStatusLegend() {
        var available = 0, hold = 0, sold = 0;
        for (var i = 0; i < plots.length; i++) {
            var k = getStatusKey(plots[i].status);
            if (k === 'sold') sold++; else if (k === 'on_hold') hold++; else available++;
        }
        var total = plots.length;
        var el = document.getElementById('status-count-total');
        if (el) el.textContent = total;
        el = document.getElementById('status-count-available');
        if (el) el.textContent = available;
        el = document.getElementById('status-count-hold');
        if (el) el.textContent = hold;
        el = document.getElementById('status-count-sold');
        if (el) el.textContent = sold;
    }

    function loadMarkers(panoramaId) {
        var pid = panoramaId != null ? String(panoramaId) : String(currentPanoramaId);
        if (!pid) return;
        markerMap.clear();
        fetch('/api/public/panoramas/' + encodeURIComponent(pid) + '/markers')
            .then(function (r) { return r.json(); })
            .then(function (rows) {
                if (String(currentPanoramaId) !== String(pid)) return;
                if (Array.isArray(rows)) {
                    for (var i = 0; i < rows.length; i++) {
                        var row = rows[i];
                        var linked = row.linked_panorama_id != null && String(row.linked_panorama_id).trim() !== '' ? Number(row.linked_panorama_id) : null;
                        markerMap.set(String(row.id), {
                            id: String(row.id),
                            name: row.name || '',
                            description: row.description || '',
                            longitude: Number(row.longitude || 0),
                            latitude: Number(row.latitude || 0),
                            linked_panorama_id: linked
                        });
                    }
                }
                syncAllMarkers();
            })
            .catch(function () {
                if (String(currentPanoramaId) !== String(pid)) return;
                syncAllMarkers();
            });
    }

    function goToPanorama(targetId) {
        var id = String(targetId || '').trim();
        if (!id) return;
        var inWorkspace = getWorkspacePanoramaById(id);
        if (inWorkspace) {
            historyStack.push(String(currentPanoramaId));
            backBtn.hidden = false;
            switchToPanorama(id);
        } else {
            var slug = ORG_SLUG || '';
            window.location.href = (slug ? '/customer/' + encodeURIComponent(slug) + '/3d/' : '/customer/3d/') + encodeURIComponent(id);
        }
    }

    function onMarkerSelect(marker) {
        var data = marker && marker.config ? marker.config.data : null;
        if (!data || data.markerType !== 'poi') return;
        var markerId = String(data.markerId);
        var m = markerMap.get(markerId);
        if (!m) return;
        if (m.linked_panorama_id && getWorkspacePanoramaById(m.linked_panorama_id)) {
            historyStack.push(String(currentPanoramaId));
            backBtn.hidden = false;
            switchToPanorama(m.linked_panorama_id);
            return;
        }
        if (markerModal) {
            document.getElementById('marker-readonly-name').textContent = m.name || 'Marker';
            document.getElementById('marker-readonly-description').textContent = m.description || '—';
            if (enterMarkerPanoramaBtn) {
                if (m.linked_panorama_id) {
                    enterMarkerPanoramaBtn.style.display = '';
                    enterMarkerPanoramaBtn.dataset.panoramaId = String(m.linked_panorama_id);
                } else {
                    enterMarkerPanoramaBtn.style.display = 'none';
                }
            }
            markerModal.classList.add('visible');
        }
    }

    function getPlotFromMarker(marker) {
        var plotId = marker && marker.config && marker.config.data ? marker.config.data.plotId : null;
        for (var i = 0; i < plots.length; i++) {
            if (String(plots[i].id) === String(plotId)) return plots[i];
        }
        return null;
    }

    function initViewer() {
        var firstUrl = getImageUrl(currentPanoramaId);
        if (!firstUrl) {
            if (loading) loading.classList.add('hidden');
            return;
        }

        viewer = new PhotoSphereViewer.Viewer({
            container: psvContainer,
            panorama: firstUrl,
            navbar: false,
            mousewheel: true,
            mousemove: true,
            loadingTxt: '',
            plugins: [[PhotoSphereViewer.MarkersPlugin, {}]]
        });

        markersPlugin = viewer.getPlugin(PhotoSphereViewer.MarkersPlugin);
        if (markersPlugin) {
            markersPlugin.on('select-marker', function (e, marker) {
                if (marker && marker.config && marker.config.data && marker.config.data.label) {
                    var plot = getPlotFromMarker(marker);
                    if (plot && plotDetailsModal) {
                        document.getElementById('details-title').textContent = plot.name || 'Plot';
                        document.getElementById('details-price').textContent = plot.price || '—';
                        document.getElementById('details-area').textContent = plot.area || '—';
                        document.getElementById('details-status').textContent = (plot.status || '—').toLowerCase().replace('_', ' ');
                        if (enterPlotPanoramaBtn) {
                            if (plot.linked_panorama_id) {
                                enterPlotPanoramaBtn.style.display = '';
                                enterPlotPanoramaBtn.dataset.panoramaId = String(plot.linked_panorama_id);
                            } else {
                                enterPlotPanoramaBtn.style.display = 'none';
                            }
                        }
                        plotDetailsModal.classList.add('visible');
                    }
                    return;
                }
                onMarkerSelect(marker);
            });
        }

        viewer.on('ready', function () {
            if (loading) loading.classList.add('hidden');
            renderPanoramaList();
            backBtn.hidden = historyStack.length === 0;

            loadPlots(currentPanoramaId);
            loadMarkers(currentPanoramaId);

            if (INITIAL_PANORAMA_ID != null && Number(INITIAL_PANORAMA_ID) !== Number(currentPanoramaId) && getWorkspacePanoramaById(INITIAL_PANORAMA_ID)) {
                switchToPanorama(INITIAL_PANORAMA_ID, true);
            }
        });
    }

    if (backBtn) {
        backBtn.addEventListener('click', function () {
            if (historyStack.length === 0) return;
            var prevId = historyStack.pop();
            backBtn.hidden = historyStack.length === 0;
            switchToPanorama(prevId, true);
        });
    }

    if (panoramaNavToggle) {
        var navOpen = true;
        panoramaNavToggle.addEventListener('click', function () {
            navOpen = !navOpen;
            if (panoramaNavShell) panoramaNavShell.classList.toggle('is-collapsed', !navOpen);
            if (panoramaNavToggleIcon) panoramaNavToggleIcon.textContent = navOpen ? '✕' : '☰';
        });
    }

    if (enterMarkerPanoramaBtn) {
        enterMarkerPanoramaBtn.addEventListener('click', function () {
            var targetId = enterMarkerPanoramaBtn.dataset && enterMarkerPanoramaBtn.dataset.panoramaId;
            if (markerModal) markerModal.classList.remove('visible');
            if (targetId) goToPanorama(targetId);
        });
    }
    if (enterPlotPanoramaBtn) {
        enterPlotPanoramaBtn.addEventListener('click', function () {
            var targetId = enterPlotPanoramaBtn.dataset && enterPlotPanoramaBtn.dataset.panoramaId;
            if (plotDetailsModal) plotDetailsModal.classList.remove('visible');
            if (targetId) goToPanorama(targetId);
        });
    }

    var closeDetails = document.getElementById('close-details');
    var closeDetailsIcon = document.getElementById('close-details-icon');
    if (closeDetails) closeDetails.addEventListener('click', function () { if (plotDetailsModal) plotDetailsModal.classList.remove('visible'); });
    if (closeDetailsIcon) closeDetailsIcon.addEventListener('click', function () { if (plotDetailsModal) plotDetailsModal.classList.remove('visible'); });

    var closeMarkerBtn = document.getElementById('close-marker-readonly-btn');
    var closeMarkerIcon = document.getElementById('close-marker-readonly');
    if (closeMarkerBtn) closeMarkerBtn.addEventListener('click', function () { if (markerModal) markerModal.classList.remove('visible'); });
    if (closeMarkerIcon) closeMarkerIcon.addEventListener('click', function () { if (markerModal) markerModal.classList.remove('visible'); });

    var viewCartBtn = document.getElementById('view-cart-btn');
    var cartModal = document.getElementById('cart-modal');
    var closeCartBtn = document.getElementById('close-cart-btn');
    var closeCart = document.getElementById('close-cart');
    if (viewCartBtn) viewCartBtn.addEventListener('click', function () { if (cartModal) cartModal.classList.add('visible'); });
    if (closeCartBtn) closeCartBtn.addEventListener('click', function () { if (cartModal) cartModal.classList.remove('visible'); });
    if (closeCart) closeCart.addEventListener('click', function () { if (cartModal) cartModal.classList.remove('visible'); });

    var exportBtn = document.getElementById('export-plots-btn');
    if (exportBtn) {
        exportBtn.addEventListener('click', function () {
            if (plots.length === 0) return alert('No plots to export.');
            var cols = ['id', 'name', 'area', 'price', 'status'];
            var header = cols.join(',');
            var rows = plots.map(function (p) {
                return cols.map(function (c) {
                    var v = p[c];
                    if (v == null) return '';
                    var s = String(v).trim();
                    if (/[,\r\n"]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
                    return s;
                }).join(',');
            });
            var csv = header + '\r\n' + rows.join('\r\n');
            var blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
            var a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'plots-' + currentPanoramaId + '.csv';
            a.click();
            URL.revokeObjectURL(a.href);
        });
    }

    var fullscreenBtn = document.getElementById('fullscreen-btn');
    if (fullscreenBtn) {
        fullscreenBtn.addEventListener('click', function () {
            if (viewer && typeof viewer.toggleFullscreen === 'function') viewer.toggleFullscreen();
        });
    }

    function boot() {
        if (typeof PhotoSphereViewer === 'undefined' || !PhotoSphereViewer.MarkersPlugin) {
            setTimeout(boot, 100);
            return;
        }
        initViewer();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
