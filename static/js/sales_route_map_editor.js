(function () {
    'use strict';

    var BOOTSTRAP = window.__SRE_BOOTSTRAP__ || {};
    var MAP_ID = BOOTSTRAP.mapId;

    var mapData = null;
    var markers = [];
    var routes = [];
    var hoverRoutes = [];
    var mode = 'select';
    var selectedMarkerId = null;
    var routeDraft = { startMarkerId: null, waypoints: [] };
    var hoverRouteDraft = { points: [] };
    var iconLibrary = window.salesRouteMapIcons || null;

    var stageEl = null;
    var imageEl = null;
    var markersLayerEl = null;
    var routesSvgEl = null;
    var statusEl = null;
    var modeLabelEl = null;
    var selectedPanelEl = null;
    var markerModalHandleEl = null;
    var markerModalCloseEl = null;
    var liveRouteSaveTimers = {};
    var liveRouteSaveQueue = {};
    var liveRouteSaveInFlight = {};
    var dragState = {
        active: false,
        markerId: null,
        pointerId: null,
        markerEl: null,
        latestRatios: null,
        moved: false,
        suppressStageClick: false
    };
    var markerModalState = {
        active: false,
        pointerId: null,
        startLeft: 0,
        startTop: 0,
        left: 0,
        top: 0,
        pointerStartX: 0,
        pointerStartY: 0,
        initialized: false
    };

    function clampRatio(value) {
        var num = Number(value);
        if (!Number.isFinite(num)) return 0;
        if (num < 0) return 0;
        if (num > 1) return 1;
        return num;
    }

    function clampLineWidth(value) {
        var num = Number(value);
        if (!Number.isFinite(num)) return 3;
        return Math.max(1, Math.min(12, Math.round(num)));
    }

    function clampMarkerSize(value) {
        var num = Number(value);
        if (!Number.isFinite(num)) return 1;
        if (num < 0.6) return 0.6;
        if (num > 2.4) return 2.4;
        return Math.round(num * 100) / 100;
    }

    function normalizeHexColor(value, fallback) {
        var raw = String(value || '').trim();
        if (/^#[0-9a-fA-F]{3}$/.test(raw)) {
            return '#' + raw[1] + raw[1] + raw[2] + raw[2] + raw[3] + raw[3];
        }
        if (/^#[0-9a-fA-F]{6}$/.test(raw)) return raw.toLowerCase();
        return fallback || '#162338';
    }

    function routeColor(route) {
        return normalizeHexColor(route && route.color, '#162338');
    }

    function routeLineWidth(route) {
        return clampLineWidth(route && route.line_width);
    }

    function routeStrokeWidthSvg(lineWidth) {
        return String(clampLineWidth(lineWidth) * 0.002);
    }

    function normalizeDistanceKm(value) {
        if (value === null || value === undefined) return null;
        var raw = String(value).trim();
        if (!raw) return null;
        var num = Number(raw);
        if (!Number.isFinite(num) || num < 0) return null;
        return Math.round(num * 1000) / 1000;
    }

    function routeDistanceKm(route) {
        return normalizeDistanceKm(route && route.distance_km);
    }

    function routeById(routeId) {
        return routes.find(function (route) { return String(route.id) === String(routeId); }) || null;
    }

    function hoverRouteById(routeId) {
        return hoverRoutes.find(function (route) { return String(route.id) === String(routeId); }) || null;
    }

    function currentDraftRouteStyle() {
        var colorInput = document.getElementById('sre-route-color');
        var widthInput = document.getElementById('sre-route-width');
        var distanceInput = document.getElementById('sre-route-distance');
        return {
            color: normalizeHexColor(colorInput ? colorInput.value : '', '#162338'),
            line_width: clampLineWidth(widthInput ? widthInput.value : 3),
            distance_km: normalizeDistanceKm(distanceInput ? distanceInput.value : null)
        };
    }

    function currentDraftHoverRouteStyle() {
        var colorInput = document.getElementById('sre-hover-route-color');
        var widthInput = document.getElementById('sre-hover-route-width');
        return {
            label: (document.getElementById('sre-hover-route-label').value || '').trim(),
            color: normalizeHexColor(colorInput ? colorInput.value : '', '#facc15'),
            line_width: clampLineWidth(widthInput ? widthInput.value : 4)
        };
    }

    function hoverRouteLabel(route, index) {
        var label = route && String(route.label || '').trim();
        if (label) return label;
        return 'Independent Route ' + (Number(index) + 1);
    }

    function escapeHtml(text) {
        return String(text || '').replace(/[&<>"']/g, function (char) {
            return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char];
        });
    }

    function authHeaders() {
        if (!window._supabase) return Promise.resolve({});
        return window._supabase.auth.getSession().then(function (result) {
            var headers = { 'Content-Type': 'application/json' };
            if (result.data.session && result.data.session.access_token) {
                headers.Authorization = 'Bearer ' + result.data.session.access_token;
            }
            return headers;
        });
    }

    async function apiFetch(url, opts) {
        var headers = await authHeaders();
        var merged = Object.assign({ headers: headers }, opts || {});
        if (merged.body instanceof FormData) delete merged.headers['Content-Type'];
        var response = await fetch(url, merged);
        if (!response.ok) {
            var payload = await response.text();
            throw new Error(payload || response.statusText);
        }
        return response.json();
    }

    function markerById(markerId) {
        return markers.find(function (marker) { return String(marker.id) === String(markerId); }) || null;
    }

    function selectedMarker() {
        return markerById(selectedMarkerId);
    }

    function markerMeta(marker) {
        if (iconLibrary && typeof iconLibrary.resolveMarkerMeta === 'function') {
            return iconLibrary.resolveMarkerMeta(marker || {});
        }
        var markerType = marker && marker.marker_type ? marker.marker_type : 'normal';
        return {
            markerType: markerType,
            pointerType: markerType === 'main' ? 'project' : 'landmark',
            iconKey: marker && marker.icon_key ? marker.icon_key : (markerType === 'main' ? 'main-star' : 'plot-pin'),
            iconColor: iconLibrary && typeof iconLibrary.defaultColor === 'function'
                ? iconLibrary.defaultColor(markerType)
                : (markerType === 'main' ? '#f97316' : '#22d3ee'),
            iconLook: 'solid',
            markerSize: 1,
            appearance: {
                color: markerType === 'main' ? '#f97316' : '#22d3ee',
                look: 'solid'
            },
            typeMeta: { label: markerType === 'main' ? 'Project / Site' : 'Landmark', shortLabel: markerType === 'main' ? 'Project' : 'Landmark' }
        };
    }

    function markerName(marker) {
        if (!marker) return '';
        var label = (marker.label || '').trim();
        if (label) return label;
        var meta = markerMeta(marker);
        return meta.typeMeta.shortLabel || (marker.marker_type === 'main' ? 'Main Pointer' : 'Pointer');
    }

    function markerPointerType(marker) {
        return markerMeta(marker).pointerType;
    }

    function markerPointerLabel(marker) {
        return markerMeta(marker).typeMeta.label;
    }

    function markerIconColor(marker) {
        return markerMeta(marker).iconColor;
    }

    function markerIconLook(marker) {
        return markerMeta(marker).iconLook;
    }

    function markerSizeValue(marker) {
        return markerMeta(marker).markerSize || 1;
    }

    function markerIconKey(marker) {
        if (!marker) {
            return iconLibrary && typeof iconLibrary.defaultForType === 'function'
                ? iconLibrary.defaultForType('normal')
                : 'plot-pin';
        }
        return markerMeta(marker).iconKey;
    }

    function markerIconSvg(marker) {
        var markerType = marker && marker.marker_type ? marker.marker_type : 'normal';
        var pointerType = marker && marker.pointer_type ? marker.pointer_type : (markerType === 'main' ? 'project' : 'landmark');
        var key = markerIconKey(marker);
        if (iconLibrary && typeof iconLibrary.renderSvg === 'function') {
            return iconLibrary.renderSvg(key, markerType, pointerType);
        }
        return '';
    }

    function applyMarkerAppearance(el, marker) {
        if (!el) return;
        if (iconLibrary && typeof iconLibrary.applyMarkerAppearance === 'function') {
            iconLibrary.applyMarkerAppearance(el, marker || {});
            return;
        }
        var meta = markerMeta(marker);
        if (meta && meta.iconColor) el.style.setProperty('--srm-marker-bg', meta.iconColor);
    }

    function markerStyleAttr(marker) {
        if (iconLibrary && typeof iconLibrary.markerStyleAttr === 'function') {
            return iconLibrary.markerStyleAttr(marker || {});
        }
        return '';
    }

    function setSelectedIconKey(iconKey) {
        var input = document.getElementById('sre-marker-icon');
        if (!input) return;
        var markerType = document.getElementById('sre-marker-type').value || 'normal';
        var pointerType = document.getElementById('sre-marker-pointer-type').value || 'landmark';
        if (!iconLibrary || typeof iconLibrary.normalizeIconKey !== 'function') {
            input.value = String(iconKey || '').trim().toLowerCase() || (markerType === 'main' ? 'main-star' : 'plot-pin');
            renderMarkerPreview();
            return;
        }
        input.value = iconLibrary.normalizeIconKey(iconKey, markerType, pointerType);
        syncIconLibrarySelection();
        renderMarkerPreview();
    }

    function syncIconLibrarySelection() {
        var input = document.getElementById('sre-marker-icon');
        if (!input) return;
        var selectedKey = String(input.value || '').trim().toLowerCase();
        document.querySelectorAll('.sre-icon-btn[data-icon-key]').forEach(function (btn) {
            btn.classList.toggle('active', String(btn.dataset.iconKey || '').toLowerCase() === selectedKey);
        });
    }

    function iconButtonHtml(icon) {
        return '<button type="button" class="sre-icon-btn" data-icon-key="' + escapeHtml(icon.key) + '" title="' + escapeHtml(icon.label) + '">' +
            '<span class="sre-icon-svg">' + icon.svg + '</span>' +
            '<span class="sre-icon-name">' + escapeHtml(icon.label) + '</span>' +
            '</button>';
    }

    function renderPointerTypeOptions(markerType, selectedPointerType) {
        var select = document.getElementById('sre-marker-pointer-type');
        if (!select) return;
        if (!iconLibrary || typeof iconLibrary.pointerTypeOptions !== 'function') {
            select.innerHTML = markerType === 'main'
                ? '<option value="project">Project / Site</option>'
                : '<option value="landmark">Landmark</option>';
            select.value = markerType === 'main' ? 'project' : 'landmark';
            select.disabled = markerType === 'main';
            return;
        }
        var options = iconLibrary.pointerTypeOptions(markerType);
        var normalizedSelected = iconLibrary.normalizePointerType(selectedPointerType, markerType);
        select.innerHTML = options.map(function (optionMeta) {
            return '<option value="' + escapeHtml(optionMeta.key) + '">' + escapeHtml(optionMeta.label) + '</option>';
        }).join('');
        select.value = normalizedSelected;
        select.disabled = markerType === 'main';
    }

    function renderMarkerLookOptions(selectedLook) {
        var select = document.getElementById('sre-marker-look');
        if (!select) return;
        var options = iconLibrary && typeof iconLibrary.markerLookOptions === 'function'
            ? iconLibrary.markerLookOptions()
            : [{ key: 'solid', label: 'Solid' }];
        var normalizedSelected = iconLibrary && typeof iconLibrary.normalizeMarkerLook === 'function'
            ? iconLibrary.normalizeMarkerLook(selectedLook)
            : (selectedLook || 'solid');
        select.innerHTML = options.map(function (optionMeta) {
            return '<option value="' + escapeHtml(optionMeta.key) + '">' + escapeHtml(optionMeta.label) + '</option>';
        }).join('');
        select.value = normalizedSelected;
    }

    function syncMarkerSizeInputs(nextValue) {
        var rangeInput = document.getElementById('sre-marker-size-range');
        var numberInput = document.getElementById('sre-marker-size');
        var normalized = clampMarkerSize(nextValue);
        var textValue = normalized.toFixed(2).replace(/0$/, '').replace(/\.0$/, '');
        if (rangeInput) rangeInput.value = String(normalized);
        if (numberInput) numberInput.value = textValue;
        return normalized;
    }

    function markerPreviewState() {
        var markerType = document.getElementById('sre-marker-type').value || 'normal';
        var pointerType = document.getElementById('sre-marker-pointer-type').value || (markerType === 'main' ? 'project' : 'landmark');
        var iconKey = document.getElementById('sre-marker-icon').value || '';
        var iconColor = normalizeHexColor(document.getElementById('sre-marker-color').value, iconLibrary && typeof iconLibrary.defaultColor === 'function' ? iconLibrary.defaultColor(markerType) : '#22d3ee');
        var iconLook = document.getElementById('sre-marker-look').value || 'solid';
        var markerSize = syncMarkerSizeInputs(document.getElementById('sre-marker-size').value || document.getElementById('sre-marker-size-range').value || 1);
        return {
            marker_type: markerType,
            pointer_type: pointerType,
            icon_key: iconKey,
            icon_color: iconColor,
            icon_look: iconLook,
            marker_size: markerSize
        };
    }

    function renderMarkerPreview() {
        var pinEl = document.getElementById('sre-marker-preview-pin');
        var iconEl = document.getElementById('sre-marker-preview-icon');
        if (!pinEl || !iconEl) return;
        var previewMarker = markerPreviewState();
        pinEl.classList.toggle('main', previewMarker.marker_type === 'main');
        iconEl.innerHTML = markerIconSvg(previewMarker);
        applyMarkerAppearance(pinEl, previewMarker);
    }

    function hasSelectedMarkerFormState() {
        var selected = selectedMarker();
        var form = document.getElementById('sre-selected-form');
        return !!selected && !!form && form.style.display !== 'none';
    }

    function markerDraftFromForm(marker) {
        if (!marker || String(marker.id) !== String(selectedMarkerId) || !hasSelectedMarkerFormState()) {
            return marker;
        }
        var markerTypeInput = document.getElementById('sre-marker-type');
        var pointerTypeInput = document.getElementById('sre-marker-pointer-type');
        var labelInput = document.getElementById('sre-marker-label');
        var iconInput = document.getElementById('sre-marker-icon');
        var colorInput = document.getElementById('sre-marker-color');
        var lookInput = document.getElementById('sre-marker-look');
        var sizeInput = document.getElementById('sre-marker-size');
        var sizeRangeInput = document.getElementById('sre-marker-size-range');
        var draftMarkerType = markerTypeInput ? (markerTypeInput.value || marker.marker_type || 'normal') : (marker.marker_type || 'normal');
        var fallbackPointerType = iconLibrary && typeof iconLibrary.defaultPointerType === 'function'
            ? iconLibrary.defaultPointerType(draftMarkerType)
            : (draftMarkerType === 'main' ? 'project' : 'landmark');
        return Object.assign({}, marker, {
            label: labelInput ? labelInput.value : marker.label,
            marker_type: draftMarkerType,
            pointer_type: pointerTypeInput ? (pointerTypeInput.value || marker.pointer_type || fallbackPointerType) : (marker.pointer_type || fallbackPointerType),
            icon_key: iconInput ? (iconInput.value || marker.icon_key || '') : marker.icon_key,
            icon_color: colorInput ? normalizeHexColor(
                colorInput.value,
                iconLibrary && typeof iconLibrary.defaultColor === 'function'
                    ? iconLibrary.defaultColor(draftMarkerType)
                    : '#22d3ee'
            ) : marker.icon_color,
            icon_look: lookInput ? (lookInput.value || marker.icon_look || 'solid') : marker.icon_look,
            marker_size: clampMarkerSize(
                sizeInput ? sizeInput.value : (sizeRangeInput ? sizeRangeInput.value : marker.marker_size)
            )
        });
    }

    function refreshSelectedMarkerDraftRender() {
        renderMarkerPreview();
        renderMapOverlay();
        renderLists();
    }

    function markerModalBounds() {
        if (!selectedPanelEl) {
            return { minLeft: 12, maxLeft: 12, minTop: 88, maxTop: 88 };
        }
        var width = selectedPanelEl.offsetWidth || 390;
        var height = selectedPanelEl.offsetHeight || 420;
        var minLeft = 12;
        var minTop = 88;
        var maxLeft = Math.max(minLeft, window.innerWidth - width - 12);
        var maxTop = Math.max(minTop, window.innerHeight - height - 12);
        return {
            minLeft: minLeft,
            maxLeft: maxLeft,
            minTop: minTop,
            maxTop: maxTop
        };
    }

    function setMarkerModalPosition(left, top) {
        if (!selectedPanelEl) return;
        var bounds = markerModalBounds();
        var nextLeft = Math.max(bounds.minLeft, Math.min(bounds.maxLeft, Number(left) || bounds.maxLeft));
        var nextTop = Math.max(bounds.minTop, Math.min(bounds.maxTop, Number(top) || bounds.minTop));
        selectedPanelEl.style.left = nextLeft + 'px';
        selectedPanelEl.style.top = nextTop + 'px';
        selectedPanelEl.style.right = 'auto';
        markerModalState.left = nextLeft;
        markerModalState.top = nextTop;
    }

    function ensureMarkerModalPosition(forceReset) {
        if (!selectedPanelEl || !selectedPanelEl.classList.contains('is-floating')) return;
        requestAnimationFrame(function () {
            if (forceReset || !markerModalState.initialized) {
                var width = selectedPanelEl.offsetWidth || 390;
                setMarkerModalPosition(window.innerWidth - width - 22, 112);
                markerModalState.initialized = true;
                return;
            }
            setMarkerModalPosition(markerModalState.left, markerModalState.top);
        });
    }

    function endMarkerModalDrag() {
        if (!markerModalState.active) return;
        markerModalState.active = false;
        markerModalState.pointerId = null;
        document.removeEventListener('pointermove', handleMarkerModalDragMove);
        document.removeEventListener('pointerup', endMarkerModalDrag);
        document.removeEventListener('pointercancel', endMarkerModalDrag);
    }

    function handleMarkerModalDragMove(event) {
        if (!markerModalState.active || event.pointerId !== markerModalState.pointerId) return;
        var nextLeft = markerModalState.startLeft + (event.clientX - markerModalState.pointerStartX);
        var nextTop = markerModalState.startTop + (event.clientY - markerModalState.pointerStartY);
        setMarkerModalPosition(nextLeft, nextTop);
    }

    function beginMarkerModalDrag(event) {
        if (!selectedPanelEl || !selectedPanelEl.classList.contains('is-floating')) return;
        if (event.target && event.target.closest && event.target.closest('button, input, select, textarea, label')) return;
        if (typeof event.button === 'number' && event.button !== 0) return;
        event.preventDefault();
        markerModalState.active = true;
        markerModalState.pointerId = event.pointerId;
        markerModalState.startLeft = markerModalState.left;
        markerModalState.startTop = markerModalState.top;
        markerModalState.pointerStartX = event.clientX;
        markerModalState.pointerStartY = event.clientY;
        document.addEventListener('pointermove', handleMarkerModalDragMove);
        document.addEventListener('pointerup', endMarkerModalDrag);
        document.addEventListener('pointercancel', endMarkerModalDrag);
    }

    function renderIconLibrary() {
        var mainGrid = document.getElementById('sre-main-icon-grid');
        var normalGrid = document.getElementById('sre-normal-icon-grid');
        if (!mainGrid || !normalGrid || !iconLibrary) return;

        var mainIcons = typeof iconLibrary.byGroup === 'function' ? iconLibrary.byGroup('main') : [];
        var normalIcons = typeof iconLibrary.byGroup === 'function' ? iconLibrary.byGroup('normal') : [];
        mainGrid.innerHTML = mainIcons.map(iconButtonHtml).join('');
        normalGrid.innerHTML = normalIcons.map(iconButtonHtml).join('');

        document.querySelectorAll('.sre-icon-btn[data-icon-key]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                setSelectedIconKey(btn.dataset.iconKey || '');
                refreshSelectedMarkerDraftRender();
            });
        });
        syncIconLibrarySelection();
    }

    function setStatus(message, tone) {
        statusEl.textContent = message;
        statusEl.classList.remove('ok', 'warn');
        if (tone === 'ok') statusEl.classList.add('ok');
        if (tone === 'warn') statusEl.classList.add('warn');
    }

    function setMode(nextMode) {
        mode = nextMode;
        if (mode !== 'draw_route') {
            routeDraft.startMarkerId = null;
            routeDraft.waypoints = [];
        }
        if (mode !== 'draw_hover_route') {
            hoverRouteDraft.points = [];
        }
        var labels = {
            select: 'Mode: Select',
            add_main: 'Mode: Add Main Pointer (click on map)',
            add_normal: 'Mode: Add Normal Pointer (click on map)',
            draw_route: 'Mode: Draw Route (start marker -> waypoints -> end marker)',
            draw_hover_route: 'Mode: Draw Hover Route (click map to add points, then save)',
            move_marker: 'Mode: Move Pointer (drag a pointer or click on new location)'
        };
        modeLabelEl.textContent = labels[mode] || 'Mode: Select';
        renderMapOverlay();
    }

    function stageRatiosFromClient(clientX, clientY) {
        var rect = imageEl.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return null;
        return {
            x: clampRatio((clientX - rect.left) / rect.width),
            y: clampRatio((clientY - rect.top) / rect.height)
        };
    }

    function stageRatiosFromEvent(event) {
        return stageRatiosFromClient(event.clientX, event.clientY);
    }

    function anchoredRoutePoints(route) {
        var fromMarker = markerById(route.from_marker_id);
        var toMarker = markerById(route.to_marker_id);
        if (!fromMarker || !toMarker) return [];
        var points = Array.isArray(route.path_points) ? route.path_points.filter(function (p) {
            return p && Number.isFinite(Number(p.x)) && Number.isFinite(Number(p.y));
        }).map(function (p) {
            return { x: clampRatio(p.x), y: clampRatio(p.y) };
        }) : [];
        if (!points.length) {
            return [
                { x: clampRatio(fromMarker.x_ratio), y: clampRatio(fromMarker.y_ratio) },
                { x: clampRatio(toMarker.x_ratio), y: clampRatio(toMarker.y_ratio) }
            ];
        }
        points[0] = { x: clampRatio(fromMarker.x_ratio), y: clampRatio(fromMarker.y_ratio) };
        points[points.length - 1] = { x: clampRatio(toMarker.x_ratio), y: clampRatio(toMarker.y_ratio) };
        return points;
    }

    function hoverRoutePoints(route) {
        return Array.isArray(route && route.path_points) ? route.path_points.filter(function (point) {
            return point && Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y));
        }).map(function (point) {
            return { x: clampRatio(point.x), y: clampRatio(point.y) };
        }) : [];
    }

    function pointsToSvg(points) {
        return points.map(function (point) { return point.x + ',' + point.y; }).join(' ');
    }

    function renderRoutesOverlay() {
        routesSvgEl.innerHTML = '';
        routes.forEach(function (route) {
            var points = anchoredRoutePoints(route);
            if (points.length < 2) return;
            var polyline = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
            polyline.setAttribute('points', pointsToSvg(points));
            polyline.setAttribute('class', 'sre-route');
            polyline.style.stroke = routeColor(route);
            polyline.style.strokeWidth = routeStrokeWidthSvg(routeLineWidth(route));
            routesSvgEl.appendChild(polyline);
        });

        hoverRoutes.forEach(function (route) {
            var points = hoverRoutePoints(route);
            if (points.length < 2) return;
            var polyline = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
            polyline.setAttribute('points', pointsToSvg(points));
            polyline.setAttribute('class', 'sre-route sre-route-hover');
            polyline.style.stroke = normalizeHexColor(route && route.color, '#facc15');
            polyline.style.strokeWidth = routeStrokeWidthSvg(route && route.line_width);
            routesSvgEl.appendChild(polyline);
        });

        if (mode === 'draw_route' && routeDraft.startMarkerId) {
            var startMarker = markerById(routeDraft.startMarkerId);
            if (startMarker) {
                var draftPoints = [{ x: clampRatio(startMarker.x_ratio), y: clampRatio(startMarker.y_ratio) }]
                    .concat(routeDraft.waypoints.map(function (point) { return { x: point.x, y: point.y }; }));
                if (draftPoints.length >= 2) {
                    var draftPolyline = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
                    var draftStyle = currentDraftRouteStyle();
                    draftPolyline.setAttribute('points', pointsToSvg(draftPoints));
                    draftPolyline.setAttribute('class', 'sre-route sre-route-draft');
                    draftPolyline.style.stroke = draftStyle.color;
                    draftPolyline.style.strokeWidth = routeStrokeWidthSvg(draftStyle.line_width);
                    routesSvgEl.appendChild(draftPolyline);
                }
            }
        }

        if (mode === 'draw_hover_route' && hoverRouteDraft.points.length >= 2) {
            var hoverDraftPolyline = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
            var hoverDraftStyle = currentDraftHoverRouteStyle();
            hoverDraftPolyline.setAttribute('points', pointsToSvg(hoverRouteDraft.points));
            hoverDraftPolyline.setAttribute('class', 'sre-route sre-route-hover sre-route-draft');
            hoverDraftPolyline.style.stroke = hoverDraftStyle.color;
            hoverDraftPolyline.style.strokeWidth = routeStrokeWidthSvg(hoverDraftStyle.line_width);
            routesSvgEl.appendChild(hoverDraftPolyline);
        }
    }

    function canDragMarkers() {
        return mode === 'move_marker';
    }

    function beginMarkerDrag(event, marker, markerBtn) {
        if (!canDragMarkers()) return;
        if (typeof event.button === 'number' && event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        selectedMarkerId = marker.id;
        document.querySelectorAll('.sre-marker.selected').forEach(function (el) {
            el.classList.remove('selected');
        });
        markerBtn.classList.add('selected');
        renderMarkerPanel();
        renderLists();
        dragState.active = true;
        dragState.markerId = String(marker.id);
        dragState.pointerId = event.pointerId;
        dragState.markerEl = markerBtn;
        dragState.latestRatios = { x: clampRatio(marker.x_ratio), y: clampRatio(marker.y_ratio) };
        dragState.moved = false;
        if (markerBtn.setPointerCapture) {
            try { markerBtn.setPointerCapture(event.pointerId); } catch (_e) {}
        }
        document.addEventListener('pointermove', handleMarkerDragMove);
        document.addEventListener('pointerup', finishMarkerDrag);
        document.addEventListener('pointercancel', finishMarkerDrag);
    }

    function handleMarkerDragMove(event) {
        if (!dragState.active || event.pointerId !== dragState.pointerId) return;
        var marker = markerById(dragState.markerId);
        if (!marker) return;
        var ratios = stageRatiosFromClient(event.clientX, event.clientY);
        if (!ratios) return;
        dragState.moved = true;
        dragState.latestRatios = ratios;
        marker.x_ratio = ratios.x;
        marker.y_ratio = ratios.y;
        if (dragState.markerEl) {
            dragState.markerEl.style.left = (ratios.x * 100) + '%';
            dragState.markerEl.style.top = (ratios.y * 100) + '%';
        }
        renderRoutesOverlay();
    }

    async function finishMarkerDrag(event) {
        if (!dragState.active || event.pointerId !== dragState.pointerId) return;
        var markerId = dragState.markerId;
        var latestRatios = dragState.latestRatios;
        var moved = dragState.moved;
        var markerEl = dragState.markerEl;
        var pointerId = dragState.pointerId;

        dragState.active = false;
        dragState.markerId = null;
        dragState.pointerId = null;
        dragState.markerEl = null;
        dragState.latestRatios = null;
        dragState.moved = false;

        if (markerEl && markerEl.releasePointerCapture && pointerId !== null) {
            try { markerEl.releasePointerCapture(pointerId); } catch (_e) {}
        }
        document.removeEventListener('pointermove', handleMarkerDragMove);
        document.removeEventListener('pointerup', finishMarkerDrag);
        document.removeEventListener('pointercancel', finishMarkerDrag);

        if (!moved || !markerId || !latestRatios) return;
        dragState.suppressStageClick = true;
        try {
            await moveMarkerById(markerId, latestRatios.x, latestRatios.y);
            setStatus('Pointer moved. Linked route endpoints were adjusted.', 'ok');
        } catch (error) {
            await loadMap();
            setStatus((error && error.message) ? error.message : 'Failed to move pointer', 'warn');
        }
    }

    function renderMarkersOverlay() {
        markersLayerEl.innerHTML = '';
        markers.forEach(function (marker) {
            var renderMarker = markerDraftFromForm(marker);
            var markerBtn = document.createElement('button');
            markerBtn.type = 'button';
            markerBtn.className = 'sre-marker ' + (renderMarker.marker_type === 'main' ? 'main' : 'normal');
            if (String(marker.id) === String(selectedMarkerId)) markerBtn.classList.add('selected');
            markerBtn.style.left = (clampRatio(renderMarker.x_ratio) * 100) + '%';
            markerBtn.style.top = (clampRatio(renderMarker.y_ratio) * 100) + '%';
            markerBtn.title = markerName(renderMarker);
            markerBtn.innerHTML = '<span class="sre-marker-icon">' + markerIconSvg(renderMarker) + '</span>';
            applyMarkerAppearance(markerBtn, renderMarker);
            markerBtn.addEventListener('pointerdown', function (event) {
                beginMarkerDrag(event, marker, markerBtn);
            });
            markerBtn.addEventListener('click', function (event) {
                event.stopPropagation();
                if (dragState.suppressStageClick) {
                    dragState.suppressStageClick = false;
                    return;
                }
                onMarkerClick(marker);
            });
            markersLayerEl.appendChild(markerBtn);
        });
    }

    function renderMapOverlay() {
        if (!mapData) return;
        renderMarkersOverlay();
        renderRoutesOverlay();
    }

    function renderMarkerPanel() {
        var selected = selectedMarker();
        var empty = document.getElementById('sre-selected-empty');
        var form = document.getElementById('sre-selected-form');
        var closeBtn = document.getElementById('sre-marker-modal-close');
        if (!selected) {
            empty.style.display = '';
            form.style.display = 'none';
            if (selectedPanelEl) {
                selectedPanelEl.classList.remove('is-floating');
                selectedPanelEl.style.left = '';
                selectedPanelEl.style.top = '';
                selectedPanelEl.style.right = '';
            }
            if (closeBtn) closeBtn.hidden = true;
            endMarkerModalDrag();
            return;
        }
        empty.style.display = 'none';
        form.style.display = '';
        if (selectedPanelEl) selectedPanelEl.classList.add('is-floating');
        if (closeBtn) closeBtn.hidden = false;
        document.getElementById('sre-marker-label').value = selected.label || '';
        document.getElementById('sre-marker-type').value = selected.marker_type || 'normal';
        renderPointerTypeOptions(selected.marker_type || 'normal', markerPointerType(selected));
        renderMarkerLookOptions(markerIconLook(selected));
        renderIconLibrary();
        setSelectedIconKey(markerIconKey(selected));
        document.getElementById('sre-marker-color').value = normalizeHexColor(
            markerIconColor(selected),
            iconLibrary && typeof iconLibrary.defaultColor === 'function'
                ? iconLibrary.defaultColor(selected.marker_type || 'normal')
                : '#22d3ee'
        );
        document.getElementById('sre-marker-look').value = markerIconLook(selected) || 'solid';
        syncMarkerSizeInputs(markerSizeValue(selected));
        renderMarkerPreview();
        ensureMarkerModalPosition(false);
    }

    function upsertLocalRoute(updatedRoute) {
        if (!updatedRoute || !updatedRoute.id) return;
        for (var i = 0; i < routes.length; i += 1) {
            if (String(routes[i].id) === String(updatedRoute.id)) {
                routes[i] = updatedRoute;
                return;
            }
        }
        routes.push(updatedRoute);
    }

    function routeLivePayload(routeId) {
        var colorInput = document.getElementById('sre-route-color-' + routeId);
        var widthInput = document.getElementById('sre-route-width-' + routeId);
        var distanceInput = document.getElementById('sre-route-distance-' + routeId);
        var color = normalizeHexColor(colorInput ? colorInput.value : '', '#162338');
        var lineWidth = clampLineWidth(widthInput ? widthInput.value : 3);
        var distanceKm = normalizeDistanceKm(distanceInput ? distanceInput.value : null);
        if (colorInput) colorInput.value = color;
        if (widthInput) widthInput.value = String(lineWidth);
        if (distanceInput) distanceInput.value = distanceKm === null ? '' : String(distanceKm);
        return {
            color: color,
            line_width: lineWidth,
            distance_km: distanceKm
        };
    }

    function applyLocalRouteLiveValues(routeId, payload) {
        var route = routeById(routeId);
        if (!route) return;
        route.color = payload.color;
        route.line_width = payload.line_width;
        route.distance_km = payload.distance_km;
    }

    async function persistRouteLiveValues(routeId) {
        if (liveRouteSaveInFlight[routeId]) return;
        var queuedPayload = liveRouteSaveQueue[routeId];
        if (!queuedPayload) return;
        liveRouteSaveQueue[routeId] = null;
        liveRouteSaveInFlight[routeId] = true;
        try {
            var updated = await apiFetch('/api/sales-map-routes/' + encodeURIComponent(routeId), {
                method: 'PATCH',
                body: JSON.stringify(queuedPayload)
            });
            upsertLocalRoute(updated);
            renderMapOverlay();
        } catch (error) {
            await loadMap();
            setStatus((error && error.message) ? error.message : 'Failed to update route style', 'warn');
            return;
        } finally {
            liveRouteSaveInFlight[routeId] = false;
        }
        if (liveRouteSaveQueue[routeId]) {
            await persistRouteLiveValues(routeId);
        }
    }

    function queueRouteLiveSave(routeId, payload) {
        if (liveRouteSaveTimers[routeId]) {
            clearTimeout(liveRouteSaveTimers[routeId]);
        }
        liveRouteSaveQueue[routeId] = payload;
        liveRouteSaveTimers[routeId] = setTimeout(function () {
            liveRouteSaveTimers[routeId] = null;
            persistRouteLiveValues(routeId).catch(function (error) {
                setStatus((error && error.message) ? error.message : 'Failed to save route style', 'warn');
            });
        }, 260);
    }

    function handleRouteLiveInput(routeId) {
        var payload = routeLivePayload(routeId);
        applyLocalRouteLiveValues(routeId, payload);
        var swatchEl = document.getElementById('sre-route-swatch-' + routeId);
        if (swatchEl) swatchEl.style.background = payload.color;
        var widthChipEl = document.getElementById('sre-route-width-chip-' + routeId);
        if (widthChipEl) widthChipEl.textContent = 'W ' + payload.line_width;
        var distanceChipEl = document.getElementById('sre-route-distance-chip-' + routeId);
        if (distanceChipEl) {
            distanceChipEl.textContent = payload.distance_km === null ? 'No distance' : (payload.distance_km + ' km');
        }
        renderMapOverlay();
        queueRouteLiveSave(routeId, payload);
    }

    async function saveHoverRouteDraft() {
        if (hoverRouteDraft.points.length < 2) {
            setStatus('Add at least two points before saving the hover route.', 'warn');
            return;
        }
        var style = currentDraftHoverRouteStyle();
        await apiFetch('/api/sales-maps/' + encodeURIComponent(MAP_ID) + '/hover-routes', {
            method: 'POST',
            body: JSON.stringify({
                label: style.label,
                path_points: hoverRouteDraft.points,
                color: style.color,
                line_width: style.line_width
            })
        });
        hoverRouteDraft.points = [];
        document.getElementById('sre-hover-route-label').value = '';
        await loadMap();
        setStatus('Independent hover route saved. Click to draw another or switch modes.', 'ok');
    }

    async function saveHoverRouteRow(routeId) {
        var labelInput = document.getElementById('sre-hover-route-label-' + routeId);
        var colorInput = document.getElementById('sre-hover-route-color-' + routeId);
        var widthInput = document.getElementById('sre-hover-route-width-' + routeId);
        var payload = {
            label: labelInput ? labelInput.value.trim() : '',
            color: normalizeHexColor(colorInput ? colorInput.value : '', '#facc15'),
            line_width: clampLineWidth(widthInput ? widthInput.value : 4)
        };
        if (colorInput) colorInput.value = payload.color;
        if (widthInput) widthInput.value = String(payload.line_width);
        await apiFetch('/api/sales-map-hover-routes/' + encodeURIComponent(routeId), {
            method: 'PATCH',
            body: JSON.stringify(payload)
        });
        await loadMap();
        setStatus('Independent hover route updated.', 'ok');
    }

    async function deleteHoverRouteRow(routeId) {
        if (!confirm('Delete this independent hover route?')) return;
        await apiFetch('/api/sales-map-hover-routes/' + encodeURIComponent(routeId), {
            method: 'DELETE'
        });
        await loadMap();
        setStatus('Independent hover route deleted.', 'ok');
    }

    function renderLists() {
        var markersList = document.getElementById('sre-markers-list');
        if (!markers.length) {
            markersList.innerHTML = '<div class="sre-note">No pointers yet.</div>';
        } else {
            markersList.innerHTML = markers.map(function (marker) {
                var renderMarker = markerDraftFromForm(marker);
                var markerType = renderMarker.marker_type === 'main' ? 'Main' : 'Normal';
                var cls = String(marker.id) === String(selectedMarkerId) ? ' active' : '';
                return '<button type="button" class="sre-list-item' + cls + '" data-marker-id="' + marker.id + '">' +
                    '<span class="sre-list-item-left"><span class="sre-mini-icon" style="' + escapeHtml(markerStyleAttr(renderMarker)) + '">' + markerIconSvg(renderMarker) + '</span><span class="sre-list-item-copy"><span>' + escapeHtml(markerName(renderMarker)) + '</span><span class="sre-chip">' + escapeHtml(markerPointerLabel(renderMarker)) + '</span></span></span><span class="sre-list-item-right"><span class="sre-chip">' + markerType + '</span></span>' +
                    '</button>';
            }).join('');
            markersList.querySelectorAll('[data-marker-id]').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    selectedMarkerId = btn.dataset.markerId;
                    renderAll();
                });
            });
        }

        var routesList = document.getElementById('sre-routes-list');
        if (!routes.length) {
            routesList.innerHTML = '<div class="sre-note">No routes yet.</div>';
        } else {
            routesList.innerHTML = routes.map(function (route) {
                var fromMarker = markerById(route.from_marker_id);
                var toMarker = markerById(route.to_marker_id);
                var label = markerName(fromMarker) + ' -> ' + markerName(toMarker);
                var color = routeColor(route);
                var lineWidth = routeLineWidth(route);
                var distanceKm = routeDistanceKm(route);
                var distanceChip = distanceKm === null ? 'No distance' : (distanceKm + ' km');
                return '<div class="sre-list-row sre-route-row">' +
                    '<div class="sre-route-top">' +
                    '<span class="sre-route-name">' + escapeHtml(label) + '</span>' +
                    '<span class="sre-route-style"><span id="sre-route-swatch-' + route.id + '" class="sre-route-swatch" style="background:' + escapeHtml(color) + ';"></span><span id="sre-route-width-chip-' + route.id + '" class="sre-chip">W ' + lineWidth + '</span><span id="sre-route-distance-chip-' + route.id + '" class="sre-chip">' + escapeHtml(distanceChip) + '</span></span>' +
                    '</div>' +
                    '<div class="sre-route-controls">' +
                    '<input type="color" id="sre-route-color-' + route.id + '" class="sre-route-color-input" data-route-live-id="' + route.id + '" value="' + escapeHtml(color) + '">' +
                    '<input type="number" id="sre-route-width-' + route.id + '" class="sre-input sre-route-width-input" data-route-live-id="' + route.id + '" min="1" max="12" step="1" value="' + lineWidth + '">' +
                    '<input type="number" id="sre-route-distance-' + route.id + '" class="sre-input sre-route-distance-input" data-route-live-id="' + route.id + '" min="0" step="0.01" placeholder="km" value="' + (distanceKm === null ? '' : escapeHtml(String(distanceKm))) + '">' +
                    '<button type="button" class="btn btn-danger btn-sm" data-route-id="' + route.id + '">Delete</button>' +
                    '</div>' +
                    '</div>';
            }).join('');
            routesList.querySelectorAll('[data-route-live-id]').forEach(function (input) {
                var routeId = input.dataset.routeLiveId;
                input.addEventListener('input', function () { handleRouteLiveInput(routeId); });
                input.addEventListener('change', function () { handleRouteLiveInput(routeId); });
            });
            routesList.querySelectorAll('[data-route-id]').forEach(function (btn) {
                btn.addEventListener('click', async function () {
                    if (!confirm('Delete this route?')) return;
                    btn.disabled = true;
                    var routeId = btn.dataset.routeId;
                    if (liveRouteSaveTimers[routeId]) {
                        clearTimeout(liveRouteSaveTimers[routeId]);
                        liveRouteSaveTimers[routeId] = null;
                    }
                    liveRouteSaveQueue[routeId] = null;
                    liveRouteSaveInFlight[routeId] = false;
                    try {
                        await apiFetch('/api/sales-map-routes/' + encodeURIComponent(routeId), { method: 'DELETE' });
                        await loadMap();
                        setStatus('Route deleted.', 'ok');
                    } catch (error) {
                        setStatus((error && error.message) ? error.message : 'Failed to delete route', 'warn');
                    } finally {
                        btn.disabled = false;
                    }
                });
            });
        }

        var hoverRoutesList = document.getElementById('sre-hover-routes-list');
        if (!hoverRoutes.length) {
            hoverRoutesList.innerHTML = '<div class="sre-note">No independent hover routes yet.</div>';
        } else {
            hoverRoutesList.innerHTML = hoverRoutes.map(function (route, index) {
                var color = normalizeHexColor(route && route.color, '#facc15');
                var lineWidth = clampLineWidth(route && route.line_width);
                var pointsCount = hoverRoutePoints(route).length;
                return '<div class="sre-list-row sre-hover-route-row">' +
                    '<div class="sre-route-top">' +
                    '<span class="sre-route-name">' + escapeHtml(hoverRouteLabel(route, index)) + '</span>' +
                    '<span class="sre-route-style"><span class="sre-route-swatch" style="background:' + escapeHtml(color) + ';"></span><span class="sre-chip">W ' + lineWidth + '</span><span class="sre-chip">' + pointsCount + ' pts</span></span>' +
                    '</div>' +
                    '<div class="sre-hover-route-controls">' +
                    '<input type="text" id="sre-hover-route-label-' + route.id + '" class="sre-input sre-hover-route-label-input" maxlength="120" value="' + escapeHtml(route.label || '') + '" placeholder="Independent route label">' +
                    '<input type="color" id="sre-hover-route-color-' + route.id + '" class="sre-route-color-input" value="' + escapeHtml(color) + '">' +
                    '<input type="number" id="sre-hover-route-width-' + route.id + '" class="sre-input sre-route-width-input" min="1" max="12" step="1" value="' + lineWidth + '">' +
                    '<button type="button" class="btn btn-secondary btn-sm" data-hover-route-save-id="' + route.id + '">Save</button>' +
                    '<button type="button" class="btn btn-danger btn-sm" data-hover-route-delete-id="' + route.id + '">Delete</button>' +
                    '</div>' +
                    '</div>';
            }).join('');
            hoverRoutesList.querySelectorAll('[data-hover-route-save-id]').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    saveHoverRouteRow(btn.dataset.hoverRouteSaveId).catch(function (error) {
                        setStatus((error && error.message) ? error.message : 'Failed to save hover route', 'warn');
                    });
                });
            });
            hoverRoutesList.querySelectorAll('[data-hover-route-delete-id]').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    deleteHoverRouteRow(btn.dataset.hoverRouteDeleteId).catch(function (error) {
                        setStatus((error && error.message) ? error.message : 'Failed to delete hover route', 'warn');
                    });
                });
            });
        }
    }

    function renderAll() {
        renderMarkerPanel();
        renderMapOverlay();
        renderLists();
    }

    async function loadMap() {
        var data = await apiFetch('/api/sales-maps/' + encodeURIComponent(MAP_ID));
        mapData = data;
        markers = data.markers || [];
        routes = data.routes || [];
        hoverRoutes = data.hover_routes || [];
        imageEl.src = data.image_url || '';
        document.getElementById('sre-map-title').textContent = data.name || 'Route Map Editor';
        document.getElementById('sre-preview-link').href = data.share_url || '#';
        if (!selectedMarker()) selectedMarkerId = null;
        renderAll();
    }

    async function createMarker(markerType, xRatio, yRatio) {
        var pointerType = iconLibrary && typeof iconLibrary.defaultPointerType === 'function'
            ? iconLibrary.defaultPointerType(markerType)
            : (markerType === 'main' ? 'project' : 'landmark');
        var defaultLabel = markerType === 'main'
            ? 'Project / Site'
            : ((iconLibrary && typeof iconLibrary.pointerTypeShortLabel === 'function'
                ? iconLibrary.pointerTypeShortLabel(pointerType, markerType)
                : 'Pointer') + ' ' + (markers.length + 1));
        var defaultIcon = iconLibrary && typeof iconLibrary.defaultIconForPointerType === 'function'
            ? iconLibrary.defaultIconForPointerType(pointerType, markerType)
            : (markerType === 'main' ? 'main-star' : 'plot-pin');
        var defaultColor = iconLibrary && typeof iconLibrary.defaultColor === 'function'
            ? iconLibrary.defaultColor(markerType)
            : (markerType === 'main' ? '#f97316' : '#22d3ee');
        await apiFetch('/api/sales-maps/' + encodeURIComponent(MAP_ID) + '/markers', {
            method: 'POST',
            body: JSON.stringify({
                marker_type: markerType,
                pointer_type: pointerType,
                label: defaultLabel,
                icon_key: defaultIcon,
                icon_color: defaultColor,
                icon_look: 'solid',
                marker_size: 1,
                x_ratio: xRatio,
                y_ratio: yRatio
            })
        });
        await loadMap();
    }

    async function moveMarkerById(markerId, xRatio, yRatio) {
        await apiFetch('/api/sales-map-markers/' + encodeURIComponent(markerId), {
            method: 'PATCH',
            body: JSON.stringify({ x_ratio: xRatio, y_ratio: yRatio })
        });
        await loadMap();
    }

    async function moveSelectedMarker(xRatio, yRatio) {
        var marker = selectedMarker();
        if (!marker) return;
        await moveMarkerById(marker.id, xRatio, yRatio);
    }

    async function saveSelectedMarker() {
        var marker = selectedMarker();
        if (!marker) return;
        var iconInput = document.getElementById('sre-marker-icon');
        var payload = {
            label: document.getElementById('sre-marker-label').value.trim(),
            marker_type: document.getElementById('sre-marker-type').value,
            pointer_type: document.getElementById('sre-marker-pointer-type').value,
            icon_key: iconInput ? iconInput.value : '',
            icon_color: document.getElementById('sre-marker-color').value,
            icon_look: document.getElementById('sre-marker-look').value,
            marker_size: clampMarkerSize(document.getElementById('sre-marker-size').value || document.getElementById('sre-marker-size-range').value || 1)
        };
        await apiFetch('/api/sales-map-markers/' + encodeURIComponent(marker.id), {
            method: 'PATCH',
            body: JSON.stringify(payload)
        });
        await loadMap();
        setStatus('Pointer updated.', 'ok');
    }

    async function deleteSelectedMarker() {
        var marker = selectedMarker();
        if (!marker) return;
        if (!confirm('Delete selected pointer? Linked routes will also be removed.')) return;
        await apiFetch('/api/sales-map-markers/' + encodeURIComponent(marker.id), { method: 'DELETE' });
        selectedMarkerId = null;
        await loadMap();
        setStatus('Pointer deleted.', 'ok');
    }

    async function finalizeDrawRoute(endMarker) {
        var startMarker = markerById(routeDraft.startMarkerId);
        if (!startMarker || !endMarker) return;
        if (String(startMarker.id) === String(endMarker.id)) return;
        var routeStyle = currentDraftRouteStyle();
        var points = [{ x: clampRatio(startMarker.x_ratio), y: clampRatio(startMarker.y_ratio) }]
            .concat(routeDraft.waypoints.map(function (waypoint) { return { x: waypoint.x, y: waypoint.y }; }))
            .concat([{ x: clampRatio(endMarker.x_ratio), y: clampRatio(endMarker.y_ratio) }]);
        await apiFetch('/api/sales-maps/' + encodeURIComponent(MAP_ID) + '/routes', {
            method: 'POST',
            body: JSON.stringify({
                from_marker_id: startMarker.id,
                to_marker_id: endMarker.id,
                path_points: points,
                color: routeStyle.color,
                line_width: routeStyle.line_width,
                distance_km: routeStyle.distance_km
            })
        });
        routeDraft.startMarkerId = null;
        routeDraft.waypoints = [];
        await loadMap();
        setStatus('Route added.', 'ok');
    }

    function onMarkerClick(marker) {
        if (mode === 'draw_route') {
            if (!routeDraft.startMarkerId) {
                routeDraft.startMarkerId = marker.id;
                routeDraft.waypoints = [];
                setStatus('Start pointer selected. Add waypoints on map, then click destination pointer.', 'ok');
            } else if (String(routeDraft.startMarkerId) !== String(marker.id)) {
                finalizeDrawRoute(marker).catch(function (error) {
                    setStatus((error && error.message) ? error.message : 'Failed to create route', 'warn');
                });
            }
            renderMapOverlay();
            return;
        }
        if (mode === 'draw_hover_route') {
            hoverRouteDraft.points.push({
                x: clampRatio(marker.x_ratio),
                y: clampRatio(marker.y_ratio)
            });
            setStatus('Hover route point added. Keep clicking to continue, then save it from the panel.', 'ok');
            renderMapOverlay();
            return;
        }
        selectedMarkerId = marker.id;
        renderAll();
    }

    async function onStageClick(event) {
        if (!mapData) return;
        if (dragState.suppressStageClick) {
            dragState.suppressStageClick = false;
            return;
        }
        var ratios = stageRatiosFromEvent(event);
        if (!ratios) return;
        try {
            if (mode === 'add_main') {
                await createMarker('main', ratios.x, ratios.y);
                setMode('select');
                setStatus('Main pointer added.', 'ok');
                return;
            }
            if (mode === 'add_normal') {
                await createMarker('normal', ratios.x, ratios.y);
                setMode('select');
                setStatus('Pointer added.', 'ok');
                return;
            }
            if (mode === 'move_marker') {
                if (!selectedMarker()) {
                    setStatus('Select a pointer before moving.', 'warn');
                    return;
                }
                await moveSelectedMarker(ratios.x, ratios.y);
                setMode('select');
                setStatus('Pointer moved.', 'ok');
                return;
            }
            if (mode === 'draw_route') {
                if (!routeDraft.startMarkerId) {
                    setStatus('Click a start pointer first.', 'warn');
                    return;
                }
                routeDraft.waypoints.push({ x: ratios.x, y: ratios.y });
                setStatus('Waypoint added. Click destination pointer to finish route.', 'ok');
                renderMapOverlay();
                return;
            }
            if (mode === 'draw_hover_route') {
                hoverRouteDraft.points.push({ x: ratios.x, y: ratios.y });
                setStatus('Hover route point added. Add more points or save the route from the panel.', 'ok');
                renderMapOverlay();
                return;
            }
            selectedMarkerId = null;
            renderAll();
        } catch (error) {
            setStatus((error && error.message) ? error.message : 'Action failed', 'warn');
        }
    }

    function bindButtons() {
        document.getElementById('sre-mode-select').addEventListener('click', function () { setMode('select'); });
        document.getElementById('sre-mode-main').addEventListener('click', function () { setMode('add_main'); });
        document.getElementById('sre-mode-normal').addEventListener('click', function () { setMode('add_normal'); });
        document.getElementById('sre-mode-route').addEventListener('click', function () { setMode('draw_route'); });
        document.getElementById('sre-mode-hover-route').addEventListener('click', function () { setMode('draw_hover_route'); });

        if (markerModalHandleEl) {
            markerModalHandleEl.addEventListener('pointerdown', beginMarkerModalDrag);
        }
        if (markerModalCloseEl) {
            markerModalCloseEl.addEventListener('click', function () {
                selectedMarkerId = null;
                renderAll();
            });
        }
        window.addEventListener('resize', function () {
            ensureMarkerModalPosition(false);
        });

        document.getElementById('sre-route-undo').addEventListener('click', function () {
            if (mode === 'draw_hover_route') {
                if (!hoverRouteDraft.points.length) return;
                hoverRouteDraft.points.pop();
                renderMapOverlay();
                return;
            }
            if (!routeDraft.waypoints.length) return;
            routeDraft.waypoints.pop();
            renderMapOverlay();
        });
        document.getElementById('sre-route-cancel').addEventListener('click', function () {
            hoverRouteDraft.points = [];
            routeDraft.startMarkerId = null;
            routeDraft.waypoints = [];
            if (mode === 'draw_route') setStatus('Route drawing canceled.', 'warn');
            if (mode === 'draw_hover_route') setStatus('Independent hover route draft canceled.', 'warn');
            renderMapOverlay();
        });

        var routeColorInput = document.getElementById('sre-route-color');
        var routeWidthInput = document.getElementById('sre-route-width');
        var routeDistanceInput = document.getElementById('sre-route-distance');
        if (routeColorInput) {
            var onRouteColorChange = function () {
                routeColorInput.value = normalizeHexColor(routeColorInput.value, '#162338');
                if (mode === 'draw_route' && routeDraft.startMarkerId) renderMapOverlay();
            };
            routeColorInput.addEventListener('input', onRouteColorChange);
            routeColorInput.addEventListener('change', onRouteColorChange);
        }
        if (routeWidthInput) {
            var onRouteWidthChange = function () {
                routeWidthInput.value = String(clampLineWidth(routeWidthInput.value));
                if (mode === 'draw_route' && routeDraft.startMarkerId) renderMapOverlay();
            };
            routeWidthInput.addEventListener('input', onRouteWidthChange);
            routeWidthInput.addEventListener('change', onRouteWidthChange);
        }
        if (routeDistanceInput) {
            routeDistanceInput.addEventListener('change', function () {
                var normalized = normalizeDistanceKm(routeDistanceInput.value);
                routeDistanceInput.value = normalized === null ? '' : String(normalized);
            });
        }

        var hoverRouteColorInput = document.getElementById('sre-hover-route-color');
        var hoverRouteWidthInput = document.getElementById('sre-hover-route-width');
        var hoverRouteSaveBtn = document.getElementById('sre-hover-route-save');
        if (hoverRouteColorInput) {
            var onHoverRouteColorChange = function () {
                hoverRouteColorInput.value = normalizeHexColor(hoverRouteColorInput.value, '#facc15');
                if (mode === 'draw_hover_route' && hoverRouteDraft.points.length) renderMapOverlay();
            };
            hoverRouteColorInput.addEventListener('input', onHoverRouteColorChange);
            hoverRouteColorInput.addEventListener('change', onHoverRouteColorChange);
        }
        if (hoverRouteWidthInput) {
            var onHoverRouteWidthChange = function () {
                hoverRouteWidthInput.value = String(clampLineWidth(hoverRouteWidthInput.value));
                if (mode === 'draw_hover_route' && hoverRouteDraft.points.length) renderMapOverlay();
            };
            hoverRouteWidthInput.addEventListener('input', onHoverRouteWidthChange);
            hoverRouteWidthInput.addEventListener('change', onHoverRouteWidthChange);
        }
        if (hoverRouteSaveBtn) {
            hoverRouteSaveBtn.addEventListener('click', function () {
                saveHoverRouteDraft().catch(function (error) {
                    setStatus((error && error.message) ? error.message : 'Failed to save hover route', 'warn');
                });
            });
        }

        document.getElementById('sre-save-marker').addEventListener('click', function () {
            saveSelectedMarker().catch(function (error) {
                setStatus((error && error.message) ? error.message : 'Failed to save pointer', 'warn');
            });
        });
        document.getElementById('sre-delete-marker').addEventListener('click', function () {
            deleteSelectedMarker().catch(function (error) {
                setStatus((error && error.message) ? error.message : 'Failed to delete pointer', 'warn');
            });
        });
        document.getElementById('sre-move-marker').addEventListener('click', function () {
            if (!selectedMarker()) {
                setStatus('Select a pointer first.', 'warn');
                return;
            }
            setMode('move_marker');
            setStatus('Drag any pointer on the map to move it. Routes update live while dragging.', 'ok');
        });

        var markerTypeSelect = document.getElementById('sre-marker-type');
        var markerLabelInput = document.getElementById('sre-marker-label');
        var pointerTypeSelect = document.getElementById('sre-marker-pointer-type');
        var markerColorInput = document.getElementById('sre-marker-color');
        var markerLookSelect = document.getElementById('sre-marker-look');
        var markerSizeRangeInput = document.getElementById('sre-marker-size-range');
        var markerSizeInput = document.getElementById('sre-marker-size');

        if (markerLabelInput) {
            markerLabelInput.addEventListener('input', function () {
                renderLists();
            });
        }

        if (markerTypeSelect) {
            markerTypeSelect.addEventListener('change', function () {
                var nextType = markerTypeSelect.value || 'normal';
                var nextPointerType = iconLibrary && typeof iconLibrary.defaultPointerType === 'function'
                    ? iconLibrary.defaultPointerType(nextType)
                    : (nextType === 'main' ? 'project' : 'landmark');
                renderPointerTypeOptions(nextType, nextPointerType);
                setSelectedIconKey(iconLibrary && typeof iconLibrary.defaultIconForPointerType === 'function'
                    ? iconLibrary.defaultIconForPointerType(nextPointerType, nextType)
                    : '');
                refreshSelectedMarkerDraftRender();
            });
        }

        if (pointerTypeSelect) {
            pointerTypeSelect.addEventListener('change', function () {
                var markerType = markerTypeSelect ? (markerTypeSelect.value || 'normal') : 'normal';
                var pointerType = pointerTypeSelect.value || 'landmark';
                setSelectedIconKey(iconLibrary && typeof iconLibrary.defaultIconForPointerType === 'function'
                    ? iconLibrary.defaultIconForPointerType(pointerType, markerType)
                    : '');
                refreshSelectedMarkerDraftRender();
            });
        }

        if (markerColorInput) {
            var onMarkerColorChange = function () {
                var markerType = markerTypeSelect ? (markerTypeSelect.value || 'normal') : 'normal';
                markerColorInput.value = normalizeHexColor(
                    markerColorInput.value,
                    iconLibrary && typeof iconLibrary.defaultColor === 'function'
                        ? iconLibrary.defaultColor(markerType)
                        : '#22d3ee'
                );
                refreshSelectedMarkerDraftRender();
            };
            markerColorInput.addEventListener('input', onMarkerColorChange);
            markerColorInput.addEventListener('change', onMarkerColorChange);
        }

        if (markerLookSelect) {
            markerLookSelect.addEventListener('change', function () {
                refreshSelectedMarkerDraftRender();
            });
        }

        function handleMarkerSizeInput(nextValue) {
            syncMarkerSizeInputs(nextValue);
            refreshSelectedMarkerDraftRender();
        }

        if (markerSizeRangeInput) {
            markerSizeRangeInput.addEventListener('input', function () {
                handleMarkerSizeInput(markerSizeRangeInput.value);
            });
            markerSizeRangeInput.addEventListener('change', function () {
                handleMarkerSizeInput(markerSizeRangeInput.value);
            });
        }

        if (markerSizeInput) {
            markerSizeInput.addEventListener('input', function () {
                handleMarkerSizeInput(markerSizeInput.value);
            });
            markerSizeInput.addEventListener('change', function () {
                handleMarkerSizeInput(markerSizeInput.value);
            });
        }
    }

    async function init() {
        stageEl = document.getElementById('sre-map-stage');
        imageEl = document.getElementById('sre-map-image');
        markersLayerEl = document.getElementById('sre-markers-layer');
        routesSvgEl = document.getElementById('sre-routes-svg');
        statusEl = document.getElementById('sre-status');
        modeLabelEl = document.getElementById('sre-mode-label');
        selectedPanelEl = document.getElementById('sre-selected-panel');
        markerModalHandleEl = document.getElementById('sre-marker-modal-handle');
        markerModalCloseEl = document.getElementById('sre-marker-modal-close');

        stageEl.addEventListener('click', onStageClick);
        renderIconLibrary();
        bindButtons();
        setMode('select');
        await loadMap();
        setStatus('Ready. Add pointers, routes, and hover-only highways.', 'ok');
    }

    window.salesRouteMapEditor = {
        init: function () {
            return init();
        }
    };
})();
