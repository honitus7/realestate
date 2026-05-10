(function () {
    'use strict';

    var BOOTSTRAP = window.__SRV_BOOTSTRAP__ || {};
    var MARKERS = Array.isArray(BOOTSTRAP.markers) ? BOOTSTRAP.markers.slice() : [];
    var ROUTES = Array.isArray(BOOTSTRAP.routes) ? BOOTSTRAP.routes.slice() : [];
    var HOVER_ROUTES = Array.isArray(BOOTSTRAP.hoverRoutes) ? BOOTSTRAP.hoverRoutes.slice() : [];
    var iconLibrary = window.salesRouteMapIcons || null;

    var stageEl = document.getElementById('srv-stage');
    var markersLayer = document.getElementById('srv-markers-layer');
    var routesSvg = document.getElementById('srv-routes-svg');
    var hoverRoutesSvg = document.getElementById('srv-hover-routes-svg');
    var hoverRoutesLayer = document.getElementById('srv-hover-routes-layer');
    var statusEl = document.getElementById('srv-status');
    var typeFiltersEl = document.getElementById('srv-type-filters');
    var routeModalEl = document.getElementById('srv-route-modal');
    var routeModalMediaEl = routeModalEl ? routeModalEl.querySelector('.srv-route-modal-media') : null;
    var routeModalIconEl = document.getElementById('srv-route-modal-icon');
    var routeModalEyebrowEl = document.getElementById('srv-route-modal-eyebrow');
    var routeModalTitleEl = document.getElementById('srv-route-modal-title');
    var routeModalPointerTypeEl = document.getElementById('srv-route-modal-pointer-type');
    var routeModalDistanceEl = document.getElementById('srv-route-modal-distance');
    var activeMarkerId = null;
    var activeAnimationFrame = null;
    var activeTypeFilter = 'all';
    var activeHoverRouteId = null;
    var hoverRouteLocked = false;

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

    function formatDistanceKm(distanceKm) {
        var distance = normalizeDistanceKm(distanceKm);
        if (distance === null) return 'Distance not added';
        return distance + ' km';
    }

    function defaultStatusText() {
        if (HOVER_ROUTES.length) {
            return 'Click a normal pointer to view its route, or hover a highway label to preview an independent route.';
        }
        return 'Click a normal pointer to view its route.';
    }

    function markerById(markerId) {
        return MARKERS.find(function (marker) { return String(marker.id) === String(markerId); }) || null;
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
            appearance: {
                color: markerType === 'main' ? '#f97316' : '#22d3ee',
                look: 'solid'
            },
            typeMeta: { label: markerType === 'main' ? 'Project / Site' : 'Landmark', shortLabel: markerType === 'main' ? 'Project' : 'Landmark' }
        };
    }

    function markerPointerType(marker) {
        return markerMeta(marker).pointerType;
    }

    function markerPointerLabel(marker) {
        return markerMeta(marker).typeMeta.label;
    }

    function markerPointerShortLabel(marker) {
        return markerMeta(marker).typeMeta.shortLabel || markerPointerLabel(marker);
    }

    function markerLabel(marker) {
        if (!marker) return '';
        var label = String(marker.label || '').trim();
        if (label) return label;
        return markerPointerShortLabel(marker);
    }

    function markerIconKey(marker) {
        return markerMeta(marker).iconKey;
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

    function markerIconSvg(marker) {
        var markerType = marker && marker.marker_type ? marker.marker_type : 'normal';
        var pointerType = marker && marker.pointer_type ? marker.pointer_type : (markerType === 'main' ? 'project' : 'landmark');
        var key = markerIconKey(marker);
        if (iconLibrary && typeof iconLibrary.renderSvg === 'function') {
            return iconLibrary.renderSvg(key, markerType, pointerType);
        }
        return '';
    }

    function getMainMarker() {
        return MARKERS.find(function (marker) { return marker.marker_type === 'main'; }) || null;
    }

    function normalizeRoutePoints(route, startMarker, endMarker, reverse) {
        var points = Array.isArray(route.path_points) ? route.path_points.filter(function (point) {
            return point && Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y));
        }).map(function (point) {
            return { x: clampRatio(point.x), y: clampRatio(point.y) };
        }) : [];
        if (reverse) points.reverse();
        if (!points.length) {
            return [
                { x: clampRatio(startMarker.x_ratio), y: clampRatio(startMarker.y_ratio) },
                { x: clampRatio(endMarker.x_ratio), y: clampRatio(endMarker.y_ratio) }
            ];
        }
        points[0] = { x: clampRatio(startMarker.x_ratio), y: clampRatio(startMarker.y_ratio) };
        points[points.length - 1] = { x: clampRatio(endMarker.x_ratio), y: clampRatio(endMarker.y_ratio) };
        return points;
    }

    function findRoute(mainMarker, targetMarker) {
        var direct = ROUTES.find(function (route) {
            return String(route.from_marker_id) === String(mainMarker.id) &&
                   String(route.to_marker_id) === String(targetMarker.id);
        });
        if (direct) return { route: direct, reverse: false };
        var reverse = ROUTES.find(function (route) {
            return String(route.from_marker_id) === String(targetMarker.id) &&
                   String(route.to_marker_id) === String(mainMarker.id);
        });
        if (reverse) return { route: reverse, reverse: true };
        return null;
    }

    function clearRouteLayer() {
        routesSvg.innerHTML = '';
        if (activeAnimationFrame) {
            cancelAnimationFrame(activeAnimationFrame);
            activeAnimationFrame = null;
        }
    }

    function clearHoverRouteLayer() {
        if (hoverRoutesSvg) hoverRoutesSvg.innerHTML = '';
    }

    function renderHoverRoutePath(route) {
        clearHoverRouteLayer();
        if (!hoverRoutesSvg || !route) return;
        var points = normalizeHoverRoutePoints(route);
        if (points.length < 2) return;
        var glow = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
        glow.setAttribute('class', 'srv-hover-path-glow');
        glow.setAttribute('points', pointsToSvg(points));
        glow.style.strokeWidth = String(routeLineWidth(route) * 0.0036);
        hoverRoutesSvg.appendChild(glow);

        var line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
        line.setAttribute('class', 'srv-hover-path');
        line.setAttribute('points', pointsToSvg(points));
        line.style.stroke = routeColor(route);
        line.style.strokeWidth = String(routeLineWidth(route) * 0.0022);
        hoverRoutesSvg.appendChild(line);
    }

    function markerLabelDirection(marker) {
        return clampRatio(marker.x_ratio) > 0.72 ? 'left' : 'right';
    }

    function markerVisible(marker) {
        if (!marker) return false;
        if (marker.marker_type === 'main') return true;
        if (activeTypeFilter === 'all') return true;
        return markerPointerType(marker) === activeTypeFilter;
    }

    function markerClass(marker) {
        var classes = ['srv-marker', marker.marker_type === 'main' ? 'main' : 'normal', 'label-' + markerLabelDirection(marker)];
        if (String(marker.id) === String(activeMarkerId)) classes.push('active');
        if (!markerVisible(marker)) classes.push('hidden');
        return classes.join(' ');
    }

    function renderMarkers() {
        markersLayer.innerHTML = '';
        MARKERS.forEach(function (marker) {
            if (!markerVisible(marker)) return;
            var button = document.createElement('button');
            button.type = 'button';
            button.className = markerClass(marker);
            button.style.left = (clampRatio(marker.x_ratio) * 100) + '%';
            button.style.top = (clampRatio(marker.y_ratio) * 100) + '%';
            button.title = markerLabel(marker);
            button.innerHTML = '<span class="srv-marker-pin"><span class="srv-marker-icon">' + markerIconSvg(marker) + '</span></span><span class="srv-marker-label">' + markerLabel(marker) + '</span>';
            applyMarkerAppearance(button, marker);
            button.addEventListener('click', function () {
                handleMarkerClick(marker);
            });
            markersLayer.appendChild(button);
        });
    }

    function renderHoverRouteChips() {
        if (!hoverRoutesLayer) return;
        hoverRoutesLayer.innerHTML = '';
        HOVER_ROUTES.forEach(function (route, index) {
            var points = normalizeHoverRoutePoints(route);
            if (points.length < 2) return;
            var anchor = hoverRouteMidpoint(points);
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'srv-hover-chip';
            button.dataset.hoverRouteId = route.id;
            if (String(route.id) === String(activeHoverRouteId)) button.classList.add('active');
            button.style.left = (anchor.x * 100) + '%';
            button.style.top = (anchor.y * 100) + '%';
            button.textContent = hoverRouteLabel(route, index);
            button.title = hoverRouteLabel(route, index);
            button.addEventListener('mouseenter', function () {
                if (hoverRouteLocked && String(activeHoverRouteId) !== String(route.id)) return;
                activeHoverRouteId = route.id;
                hoverRouteLocked = false;
                renderHoverRoutePath(route);
                syncHoverRouteChipState();
                statusEl.textContent = 'Previewing independent route: ' + hoverRouteLabel(route, index);
            });
            button.addEventListener('mouseleave', function () {
                if (hoverRouteLocked && String(activeHoverRouteId) === String(route.id)) return;
                activeHoverRouteId = null;
                clearHoverRouteLayer();
                syncHoverRouteChipState();
                if (!activeMarkerId) statusEl.textContent = defaultStatusText();
            });
            button.addEventListener('focus', function () {
                activeHoverRouteId = route.id;
                hoverRouteLocked = false;
                renderHoverRoutePath(route);
                syncHoverRouteChipState();
                statusEl.textContent = 'Previewing independent route: ' + hoverRouteLabel(route, index);
            });
            button.addEventListener('blur', function () {
                if (hoverRouteLocked && String(activeHoverRouteId) === String(route.id)) return;
                activeHoverRouteId = null;
                clearHoverRouteLayer();
                syncHoverRouteChipState();
                if (!activeMarkerId) statusEl.textContent = defaultStatusText();
            });
            button.addEventListener('click', function () {
                if (hoverRouteLocked && String(activeHoverRouteId) === String(route.id)) {
                    hoverRouteLocked = false;
                    activeHoverRouteId = null;
                    clearHoverRouteLayer();
                    syncHoverRouteChipState();
                    if (!activeMarkerId) statusEl.textContent = defaultStatusText();
                    return;
                }
                activeHoverRouteId = route.id;
                hoverRouteLocked = true;
                renderHoverRoutePath(route);
                syncHoverRouteChipState();
                statusEl.textContent = 'Pinned independent route: ' + hoverRouteLabel(route, index);
            });
            hoverRoutesLayer.appendChild(button);
        });
    }

    function syncHoverRouteChipState() {
        if (!hoverRoutesLayer) return;
        hoverRoutesLayer.querySelectorAll('.srv-hover-chip[data-hover-route-id]').forEach(function (button) {
            button.classList.toggle('active', String(button.dataset.hoverRouteId || '') === String(activeHoverRouteId || ''));
        });
    }

    function pointsToSvg(points) {
        return points.map(function (point) { return point.x + ',' + point.y; }).join(' ');
    }

    function hoverRouteLabel(route, index) {
        var label = route && String(route.label || '').trim();
        if (label) return label;
        return 'Independent Route ' + (Number(index) + 1);
    }

    function normalizeHoverRoutePoints(route) {
        return Array.isArray(route && route.path_points) ? route.path_points.filter(function (point) {
            return point && Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y));
        }).map(function (point) {
            return { x: clampRatio(point.x), y: clampRatio(point.y) };
        }) : [];
    }

    function hoverRouteMidpoint(points) {
        if (!points.length) return { x: 0.5, y: 0.5 };
        if (points.length === 1) return points[0];
        var segments = [];
        var total = 0;
        for (var i = 1; i < points.length; i += 1) {
            var dx = points[i].x - points[i - 1].x;
            var dy = points[i].y - points[i - 1].y;
            var length = Math.sqrt((dx * dx) + (dy * dy));
            segments.push({ index: i, length: length });
            total += length;
        }
        if (!total) return points[Math.floor(points.length / 2)] || points[0];
        var halfway = total / 2;
        var walked = 0;
        for (var s = 0; s < segments.length; s += 1) {
            var segment = segments[s];
            if ((walked + segment.length) >= halfway) {
                var prev = points[segment.index - 1];
                var next = points[segment.index];
                var local = (halfway - walked) / (segment.length || 1);
                return {
                    x: clampRatio(prev.x + ((next.x - prev.x) * local)),
                    y: clampRatio(prev.y + ((next.y - prev.y) * local))
                };
            }
            walked += segment.length;
        }
        return points[points.length - 1];
    }

    function animateRoute(points, style) {
        clearRouteLayer();
        var strokeColor = normalizeHexColor(style && style.color, '#162338');
        var strokeWidth = routeStrokeWidthSvg(style && style.line_width);

        var polyline = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
        polyline.setAttribute('class', 'srv-path');
        polyline.setAttribute('points', pointsToSvg(points));
        polyline.style.stroke = strokeColor;
        polyline.style.strokeWidth = strokeWidth;
        routesSvg.appendChild(polyline);

        var length = polyline.getTotalLength();
        polyline.style.strokeDasharray = String(length);
        polyline.style.strokeDashoffset = String(length);
        polyline.getBoundingClientRect();
        polyline.style.transition = 'stroke-dashoffset 900ms ease';
        polyline.style.strokeDashoffset = '0';

        var dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        dot.setAttribute('class', 'srv-dot');
        dot.setAttribute('r', '0.01');
        dot.style.fill = strokeColor;
        routesSvg.appendChild(dot);

        var startedAt = null;
        var duration = 900;
        function tick(timestamp) {
            if (!startedAt) startedAt = timestamp;
            var progress = Math.min(1, (timestamp - startedAt) / duration);
            var point = polyline.getPointAtLength(length * progress);
            dot.setAttribute('cx', point.x);
            dot.setAttribute('cy', point.y);
            if (progress < 1) {
                activeAnimationFrame = requestAnimationFrame(tick);
            } else {
                activeAnimationFrame = null;
            }
        }
        activeAnimationFrame = requestAnimationFrame(tick);
    }

    function showRouteModal(fromMarker, toMarker, distanceKm) {
        if (!routeModalEl) return;
        routeModalIconEl.innerHTML = markerIconSvg(toMarker);
        applyMarkerAppearance(routeModalEl, toMarker);
        if (routeModalMediaEl) applyMarkerAppearance(routeModalMediaEl, toMarker);
        if (routeModalIconEl) applyMarkerAppearance(routeModalIconEl, toMarker);
        routeModalEyebrowEl.textContent = markerLabel(fromMarker) + ' -> Route';
        routeModalTitleEl.textContent = markerLabel(toMarker);
        routeModalPointerTypeEl.textContent = markerPointerLabel(toMarker);
        routeModalDistanceEl.textContent = formatDistanceKm(distanceKm);
        routeModalEl.hidden = false;
    }

    function hideRouteModal() {
        if (routeModalEl) routeModalEl.hidden = true;
    }

    function clearActiveMarkerSelection(shouldResetStatus) {
        var hadActiveMarker = !!activeMarkerId;
        activeMarkerId = null;
        if (hadActiveMarker) {
            renderMarkers();
        }
        clearRouteLayer();
        hideRouteModal();
        if (shouldResetStatus && !activeHoverRouteId) {
            statusEl.textContent = defaultStatusText();
        }
    }

    function distinctPointerTypes() {
        var seen = {};
        var types = [];
        MARKERS.forEach(function (marker) {
            if (marker.marker_type === 'main') return;
            var typeKey = markerPointerType(marker);
            if (!typeKey || seen[typeKey]) return;
            seen[typeKey] = true;
            types.push({ key: typeKey, label: markerPointerShortLabel(marker) });
        });
        return types.sort(function (a, b) {
            return a.label.localeCompare(b.label);
        });
    }

    function renderTypeFilters() {
        if (!typeFiltersEl) return;
        var types = distinctPointerTypes();
        if (!types.length) {
            typeFiltersEl.hidden = true;
            return;
        }
        typeFiltersEl.hidden = false;
        var allTypes = [{ key: 'all', label: 'All' }].concat(types);
        typeFiltersEl.innerHTML = allTypes.map(function (typeMeta) {
            var cls = 'srv-filter-chip';
            if (typeMeta.key === activeTypeFilter) cls += ' active';
            return '<button type="button" class="' + cls + '" data-pointer-type="' + typeMeta.key + '">' + typeMeta.label + '</button>';
        }).join('');
        typeFiltersEl.querySelectorAll('[data-pointer-type]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                activeTypeFilter = btn.dataset.pointerType || 'all';
                if (activeMarkerId) {
                    var activeMarker = markerById(activeMarkerId);
                    if (activeMarker && !markerVisible(activeMarker)) {
                        clearActiveMarkerSelection(false);
                    }
                }
                renderTypeFilters();
                renderMarkers();
                if (!activeHoverRouteId) {
                    statusEl.textContent = activeTypeFilter === 'all'
                        ? defaultStatusText()
                        : ('Showing only ' + btn.textContent + ' pointers.');
                }
            });
        });
    }

    function handleMarkerClick(targetMarker) {
        if (!targetMarker || targetMarker.marker_type === 'main') return;
        if (String(activeMarkerId || '') === String(targetMarker.id || '')) {
            clearActiveMarkerSelection(true);
            return;
        }
        hoverRouteLocked = false;
        activeHoverRouteId = null;
        clearHoverRouteLayer();
        syncHoverRouteChipState();
        var mainMarker = getMainMarker();
        if (!mainMarker) {
            statusEl.textContent = 'No main pointer is configured on this map yet.';
            return;
        }
        activeMarkerId = targetMarker.id;
        renderMarkers();

        var routeMatch = findRoute(mainMarker, targetMarker);
        var points;
        var style = { color: '#162338', line_width: 3 };
        var distanceKm = null;
        if (routeMatch) {
            points = normalizeRoutePoints(routeMatch.route, mainMarker, targetMarker, routeMatch.reverse);
            style = { color: routeColor(routeMatch.route), line_width: routeLineWidth(routeMatch.route) };
            distanceKm = routeDistanceKm(routeMatch.route);
            statusEl.textContent = 'Showing route: ' + markerLabel(mainMarker) + ' -> ' + markerLabel(targetMarker);
        } else {
            points = [
                { x: clampRatio(mainMarker.x_ratio), y: clampRatio(mainMarker.y_ratio) },
                { x: clampRatio(targetMarker.x_ratio), y: clampRatio(targetMarker.y_ratio) }
            ];
            statusEl.textContent = 'Direct route shown (custom route not configured).';
        }
        showRouteModal(mainMarker, targetMarker, distanceKm);
        animateRoute(points, style);
    }

    function handleStageClick(event) {
        if (!stageEl) return;
        if (event.target && event.target.closest && event.target.closest('.srv-marker, .srv-hover-chip, .srv-route-modal')) {
            return;
        }
        clearActiveMarkerSelection(true);
    }

    function init() {
        var mainMarker = getMainMarker();
        if (!mainMarker) {
            statusEl.textContent = 'Main pointer missing. Please ask your builder to configure one.';
        } else {
            statusEl.textContent = defaultStatusText();
        }
        if (stageEl) stageEl.addEventListener('click', handleStageClick);
        renderTypeFilters();
        renderMarkers();
        renderHoverRouteChips();
    }

    init();
})();
