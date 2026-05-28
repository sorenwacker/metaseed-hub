/**
 * Spec Builder - ERD Graph Visualization
 *
 * Manages the entity-relationship diagram for the spec builder interface.
 */

window.SpecBuilder = (function() {
    'use strict';

    // =========================================================================
    // Constants
    // =========================================================================

    var NODE_COLORS = {
        root: {
            background: '#4a7c59',
            border: '#2d5a4a',
            fontColor: '#fff',
            highlight: { background: '#87a878', border: '#4a7c59' },
            hover: { background: '#5a8c69', border: '#4a7c59' }
        },
        regular: {
            background: '#ffffff',
            border: '#4a7c59',
            fontColor: '#2c3e35',
            highlight: { background: '#87a878', border: '#4a7c59' },
            hover: { background: '#f5f2ed', border: '#4a7c59' }
        },
        hidden: {
            background: '#e0e0e0',
            border: '#b0b0b0',
            fontColor: '#999999',
            highlight: { background: '#d0d0d0', border: '#a0a0a0' },
            hover: { background: '#d5d5d5', border: '#a5a5a5' }
        }
    };

    var EDGE_COLORS = {
        nested: { color: '#4a7c59', highlight: '#2d5a4a' },
        reference: { color: '#7c4a6b', highlight: '#5a2d4a' }
    };

    var FONT_CONFIG = {
        face: 'monospace',
        size: 12,
        align: 'left',
        multi: 'html'
    };

    var LAYOUT = {
        nodeBaseHeight: 40,
        fieldHeight: 16,
        nodeWidth: 200,
        gridSpacing: 200
    };

    // =========================================================================
    // State
    // =========================================================================

    var network = null;
    var nodes = null;
    var edges = null;
    var selectedNode = null;
    var pendingPosition = null;
    var hiddenEntities = new Set();
    var originalNodeColors = {};

    // =========================================================================
    // Helpers
    // =========================================================================

    function getDraftId() {
        return window.draftId || '';
    }

    function apiUrl(path) {
        var draftId = getDraftId();
        if (!draftId) {
            console.error('No draft ID available');
            return '/hub/spec-builder' + path;
        }
        return '/hub/spec-builder/' + draftId + path;
    }

    // =========================================================================
    // Initialization
    // =========================================================================

    function initERD() {
        if (network) return;

        var container = document.getElementById('erd-canvas');
        if (!container) return;

        var rect = container.getBoundingClientRect();
        if (rect.height < 50) {
            setTimeout(initERD, 200);
            return;
        }

        var entityNames = Object.keys(window.entities || {});
        if (entityNames.length === 0) {
            container.innerHTML = '<div class="empty-canvas-message">Double-click to add an entity</div>';
            return;
        }

        var graphData = buildGraphData(entityNames);
        createNetwork(container, graphData.nodeData, graphData.edgeData);
        attachNetworkEventHandlers();
    }

    function buildGraphData(entityNames) {
        var nodeData = [];
        var edgeData = [];

        entityNames.forEach(function(name) {
            var entity = window.entities[name];
            var isRoot = name === window.rootEntity;
            var fields = entity.fields || [];

            var nodeConfig = buildNodeConfig(name, entity, isRoot, fields);
            storeOriginalColors(name, nodeConfig);
            nodeData.push(nodeConfig);

            var entityEdges = buildEntityEdges(name, fields);
            edgeData.push.apply(edgeData, entityEdges);
        });

        return { nodeData: nodeData, edgeData: edgeData };
    }

    function buildNodeConfig(name, entity, isRoot, fields) {
        var label = buildNodeLabel(name, isRoot, fields);
        var colors = isRoot ? NODE_COLORS.root : NODE_COLORS.regular;
        var nodeHeight = LAYOUT.nodeBaseHeight + fields.length * LAYOUT.fieldHeight;

        return {
            id: name,
            label: label,
            shape: 'box',
            size: Math.max(nodeHeight, LAYOUT.nodeWidth) / 2,
            mass: 1 + fields.length * 0.3,
            font: Object.assign({}, FONT_CONFIG, { color: colors.fontColor }),
            color: {
                background: colors.background,
                border: colors.border,
                highlight: colors.highlight,
                hover: colors.hover
            },
            borderWidth: 2,
            margin: 15,
            shadow: true
        };
    }

    function buildNodeLabel(name, isRoot, fields) {
        var label = '<b>' + name + '</b>';
        if (isRoot) label += ' [ROOT]';
        label += '\n────────────────';

        fields.forEach(function(field) {
            var req = field.required ? '*' : ' ';
            var fk = ((field.type === 'entity' || field.type === 'list') && field.items) ? '→' : ' ';
            label += '\n' + req + fk + ' ' + field.name + ': ' + field.type;
        });

        if (fields.length === 0) {
            label += '\n(no fields)';
        }

        return label;
    }

    function storeOriginalColors(name, nodeConfig) {
        originalNodeColors[name] = {
            background: nodeConfig.color.background,
            border: nodeConfig.color.border,
            fontColor: nodeConfig.font.color
        };
    }

    function buildEntityEdges(name, fields) {
        var edgeData = [];

        fields.forEach(function(field) {
            if ((field.type === 'entity' || field.type === 'list') && field.items && window.entities[field.items]) {
                edgeData.push(createEdge(name, field.items, field.name, 'nested'));
            }
            if (field.reference) {
                var targetEntity = field.reference.split('.')[0];
                if (window.entities[targetEntity]) {
                    edgeData.push(createEdge(name, targetEntity, field.name, 'reference'));
                }
            }
        });

        return edgeData;
    }

    function createEdge(from, to, label, type) {
        var colors = type === 'reference' ? EDGE_COLORS.reference : EDGE_COLORS.nested;
        var fontColor = type === 'reference' ? '#6b5a62' : '#5a6b62';

        return {
            from: from,
            to: to,
            label: label,
            arrows: { to: { enabled: true, type: 'arrow' } },
            color: colors,
            font: { size: 11, color: fontColor, background: 'white', strokeWidth: 0 },
            smooth: { type: 'cubicBezier', roundness: 0.4 },
            width: 2,
            dashes: type === 'reference'
        };
    }

    function createNetwork(container, nodeData, edgeData) {
        nodes = new vis.DataSet(nodeData);
        edges = new vis.DataSet(edgeData);

        var options = {
            layout: {
                improvedLayout: true,
                randomSeed: 42
            },
            physics: GraphConfig.getPhysicsConfig(),
            interaction: {
                hover: true,
                zoomView: true,
                zoomSpeed: 0.5,
                dragView: true,
                dragNodes: true,
                navigationButtons: false,
                keyboard: { enabled: false }
            },
            nodes: {
                shape: 'box',
                margin: 12,
                widthConstraint: { minimum: 180 }
            },
            edges: {
                smooth: {
                    type: 'cubicBezier',
                    forceDirection: 'vertical',
                    roundness: 0.4
                }
            }
        };

        network = new vis.Network(container, { nodes: nodes, edges: edges }, options);
    }

    function attachNetworkEventHandlers() {
        network.on('click', function(params) {
            if (params.nodes.length > 0) {
                selectEntity(params.nodes[0]);
            }
        });

        network.on('doubleClick', function(params) {
            if (params.nodes.length === 0) {
                showAddEntityModal();
            }
        });

        network.once('stabilizationIterationsDone', function() {
            network.setOptions({ physics: { enabled: false } });
        });

        network.on('oncontext', function(params) {
            params.event.preventDefault();
            var nodeId = network.getNodeAt(params.pointer.DOM);
            if (nodeId) {
                showContextMenu(params.event, nodeId);
            }
        });

        network.on('zoom', function(params) {
            updateZoomHint(params.scale);
        });
    }

    function updateZoomHint(scale) {
        var hint = document.getElementById('zoom-hint');
        if (!hint) return;
        if (scale < 0.5) {
            hint.classList.remove('hidden');
        } else {
            hint.classList.add('hidden');
        }
    }

    // =========================================================================
    // Entity Selection & Editor Panel
    // =========================================================================

    function selectEntity(entityName) {
        selectedNode = entityName;
        document.getElementById('editor-title').textContent = entityName;
        document.getElementById('editor-panel').classList.add('open');
        htmx.ajax('GET', apiUrl('/entity/' + entityName), {
            target: '#editor-content',
            swap: 'innerHTML'
        });
    }

    function closeEditorPanel() {
        document.getElementById('editor-panel').classList.remove('open');
        if (network) network.unselectAll();
        selectedNode = null;
    }

    // =========================================================================
    // Graph Controls
    // =========================================================================

    function refreshGraph() {
        var draftId = getDraftId();
        if (draftId) {
            window.location.href = '/hub/spec-builder/' + draftId;
        } else {
            window.location.href = '/hub/spec-builder';
        }
    }

    function deleteEntity(entityName) {
        if (!confirm("Delete entity '" + entityName + "'? This will remove all fields and relationships.")) {
            return;
        }

        fetch(apiUrl('/entity/' + encodeURIComponent(entityName)), { method: 'DELETE' })
            .then(function(response) {
                if (response.ok) {
                    refreshGraph();
                } else {
                    alert('Failed to delete entity');
                }
            })
            .catch(function(err) {
                console.error('Error deleting entity:', err);
                alert('Failed to delete entity: ' + err.message);
            });
    }

    function updateEntity(event, oldName) {
        event.preventDefault();

        var form = event.target;
        var formData = new FormData(form);
        var newName = formData.get('name').trim();
        var description = formData.get('description') || '';
        var ontologyTerm = formData.get('ontology_term') || '';

        fetch(apiUrl('/entity/' + encodeURIComponent(oldName)), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: 'new_name=' + encodeURIComponent(newName) +
                  '&description=' + encodeURIComponent(description) +
                  '&ontology_term=' + encodeURIComponent(ontologyTerm)
        })
        .then(function(response) {
            if (response.ok && newName !== oldName) {
                refreshGraph();
            } else {
                return response.text().then(function(html) {
                    document.getElementById('editor-content').innerHTML = html;
                });
            }
        })
        .catch(function(err) {
            console.error('Error updating entity:', err);
            alert('Failed to update entity: ' + err.message);
        });

        return false;
    }

    function rebuildGraph() {
        var container = document.getElementById('erd-canvas');
        if (!container) return;

        var entityNames = Object.keys(window.entities || {});

        if (entityNames.length === 0) {
            if (network) {
                network.destroy();
                network = null;
            }
            container.innerHTML = '<div class="empty-canvas-message">Double-click to add an entity</div>';
            return;
        }

        var emptyMsg = container.querySelector('.empty-canvas-message');
        if (emptyMsg) emptyMsg.remove();

        var graphData = buildGraphData(entityNames);

        if (network) {
            nodes.clear();
            edges.clear();
            nodes.add(graphData.nodeData);
            edges.add(graphData.edgeData);
            setTimeout(function() { network.fit({ animation: true }); }, 100);
        } else {
            createNetwork(container, graphData.nodeData, graphData.edgeData);
            attachNetworkEventHandlers();
        }
    }

    function autoLayout() {
        if (!network) return;

        network.setOptions({ physics: GraphConfig.getPhysicsConfig() });

        network.once('stabilizationIterationsDone', function() {
            network.setOptions({ physics: { enabled: false } });
            network.fit({ animation: { duration: 300 } });
        });

        network.stabilize();
    }

    function zoomIn() {
        if (network) {
            var scale = network.getScale();
            network.moveTo({ scale: scale * 1.15, animation: { duration: 200 } });
        }
    }

    function zoomOut() {
        if (network) {
            var scale = network.getScale();
            network.moveTo({ scale: scale / 1.15, animation: { duration: 200 } });
        }
    }

    function fitGraph() {
        if (network) {
            network.fit({ animation: { duration: 300 } });
        }
    }

    // =========================================================================
    // Add Entity Modal
    // =========================================================================

    function showAddEntityModal() {
        document.getElementById('add-entity-modal').classList.remove('hidden');
        document.getElementById('new-entity-name').value = '';
        document.getElementById('new-entity-name').focus();
    }

    function hideAddEntityModal() {
        document.getElementById('add-entity-modal').classList.add('hidden');
        pendingPosition = null;
    }

    function onEntityAdded(event) {
        if (event && event.detail && !event.detail.successful) {
            console.error('Entity add request failed:', event.detail);
            return;
        }
        hideAddEntityModal();
        setTimeout(refreshGraph, 100);
    }

    function submitAddEntityForm(event) {
        event.preventDefault();

        var nameInput = document.getElementById('new-entity-name');
        var name = nameInput.value.trim();

        if (!name) return false;

        fetch(apiUrl('/entity'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: 'name=' + encodeURIComponent(name)
        })
        .then(function(response) { return response.text(); })
        .then(function(html) {
            document.getElementById('editor-content').innerHTML = html;
            hideAddEntityModal();
            setTimeout(refreshGraph, 100);
        })
        .catch(function(err) {
            console.error('Error adding entity:', err);
            alert('Failed to add entity: ' + err.message);
        });

        return false;
    }

    function saveSpec() {
        var formData = new FormData();
        var fields = ['name', 'version', 'display_name', 'description', 'root_entity', 'ontology'];

        fields.forEach(function(field) {
            var input = document.querySelector('[name="' + field + '"]');
            if (input) formData.append(field, input.value);
        });

        fetch(apiUrl('/save'), { method: 'POST', body: formData })
            .then(function(response) { return response.text(); })
            .then(function(html) {
                var container = document.getElementById('notification-container');
                container.insertAdjacentHTML('beforeend', html);
                setTimeout(function() {
                    var notification = container.querySelector('.notification-success');
                    if (notification) notification.remove();
                }, 5000);
            })
            .catch(function(err) {
                alert('Save failed: ' + err.message);
            });
    }

    function startAddRelationship() {
        alert('To add a relationship:\n1. Click on the source entity\n2. Add a field of type "entity" or "list"\n3. Set the Items/Target to the target entity name');
    }

    // =========================================================================
    // Drag & Drop
    // =========================================================================

    function dragNewEntity(event) {
        event.dataTransfer.setData('text/plain', 'new-entity');
    }

    function dropNewEntity(event) {
        event.preventDefault();
        if (event.dataTransfer.getData('text/plain') === 'new-entity') {
            var rect = document.getElementById('erd-canvas').getBoundingClientRect();
            pendingPosition = {
                x: event.clientX - rect.left,
                y: event.clientY - rect.top
            };
            showAddEntityModal();
        }
    }

    function addEntityAtPosition(event) {
        if (event.target.id === 'erd-canvas' || event.target.tagName === 'CANVAS') {
            pendingPosition = { x: event.offsetX, y: event.offsetY };
            showAddEntityModal();
        }
    }

    // =========================================================================
    // Preview Modal
    // =========================================================================

    function showPreview() {
        document.getElementById('preview-overlay').classList.remove('hidden');
    }

    function hidePreview(event) {
        if (!event || event.target.id === 'preview-overlay') {
            document.getElementById('preview-overlay').classList.add('hidden');
        }
    }

    // =========================================================================
    // Validation Rule Modal
    // =========================================================================

    function showRuleModal(ruleIdx, ruleName) {
        document.getElementById('rule-modal-title').textContent = ruleName ? 'Edit: ' + ruleName : 'Edit Rule';
        document.getElementById('validation-rule-modal').classList.remove('hidden');
        htmx.ajax('GET', apiUrl('/validation-rule/' + ruleIdx), {
            target: '#rule-modal-content',
            swap: 'innerHTML'
        });
    }

    function hideRuleModal() {
        document.getElementById('validation-rule-modal').classList.add('hidden');
        htmx.ajax('GET', apiUrl('/validation-rules'), {
            target: '#validation-rules-panel',
            swap: 'innerHTML'
        });
    }

    // =========================================================================
    // Context Menu (Hide/Show Entities)
    // =========================================================================

    function showContextMenu(event, nodeId) {
        var menu = document.getElementById('node-context-menu');
        var isHidden = hiddenEntities.has(nodeId);

        document.getElementById('ctx-node-name').textContent = nodeId;
        document.getElementById('ctx-hide-btn').style.display = isHidden ? 'none' : 'block';
        document.getElementById('ctx-show-btn').style.display = isHidden ? 'block' : 'none';

        menu.style.left = event.pageX + 'px';
        menu.style.top = event.pageY + 'px';
        menu.classList.remove('hidden');
        menu.dataset.nodeId = nodeId;

        setTimeout(function() {
            document.addEventListener('click', closeContextMenu, { once: true });
        }, 0);
    }

    function closeContextMenu() {
        document.getElementById('node-context-menu').classList.add('hidden');
    }

    function hideEntity(nodeId) {
        if (!nodeId) nodeId = document.getElementById('node-context-menu').dataset.nodeId;
        hiddenEntities.add(nodeId);

        var colors = NODE_COLORS.hidden;
        nodes.update({
            id: nodeId,
            color: {
                background: colors.background,
                border: colors.border,
                highlight: colors.highlight,
                hover: colors.hover
            },
            font: Object.assign({}, FONT_CONFIG, { color: colors.fontColor }),
            opacity: 0.5
        });

        var connectedEdges = edges.get().filter(function(e) {
            return e.from === nodeId || e.to === nodeId;
        });
        connectedEdges.forEach(function(edge) {
            edges.update({ id: edge.id, hidden: true });
        });

        updateHiddenCount();
        closeContextMenu();
    }

    function showEntity(nodeId) {
        if (!nodeId) nodeId = document.getElementById('node-context-menu').dataset.nodeId;
        hiddenEntities.delete(nodeId);

        var original = originalNodeColors[nodeId];
        var isRoot = nodeId === window.rootEntity;
        var colors = isRoot ? NODE_COLORS.root : NODE_COLORS.regular;

        nodes.update({
            id: nodeId,
            color: {
                background: original.background,
                border: original.border,
                highlight: colors.highlight,
                hover: colors.hover
            },
            font: Object.assign({}, FONT_CONFIG, { color: original.fontColor }),
            opacity: 1
        });

        var connectedEdges = edges.get().filter(function(e) {
            return e.from === nodeId || e.to === nodeId;
        });
        connectedEdges.forEach(function(edge) {
            var otherNode = edge.from === nodeId ? edge.to : edge.from;
            if (!hiddenEntities.has(otherNode)) {
                edges.update({ id: edge.id, hidden: false });
            }
        });

        updateHiddenCount();
        closeContextMenu();
    }

    function showAllEntities() {
        hiddenEntities.forEach(function(nodeId) {
            showEntity(nodeId);
        });
        hiddenEntities.clear();
        updateHiddenCount();
    }

    function updateHiddenCount() {
        var badge = document.getElementById('hidden-count-badge');
        if (!badge) return;
        var count = hiddenEntities.size;
        if (count > 0) {
            badge.textContent = count + ' hidden';
            badge.classList.remove('hidden');
        } else {
            badge.classList.add('hidden');
        }
    }

    // =========================================================================
    // Sidebar Toggle
    // =========================================================================

    function toggleLeftSidebar() {
        var sidebar = document.getElementById('left-sidebar');
        var body = document.querySelector('.spec-builder-body');
        sidebar.classList.toggle('collapsed');
        body.classList.toggle('sidebar-collapsed');
        setTimeout(function() {
            if (network) network.fit({ animation: { duration: 200 } });
        }, 250);
    }

    // =========================================================================
    // Sidebar Tab Switching
    // =========================================================================

    function switchSidebarTab(tabName) {
        document.querySelectorAll('.sidebar-tab').forEach(function(tab) {
            tab.classList.toggle('active', tab.dataset.tab === tabName);
        });
        document.querySelectorAll('.sidebar-tab-content').forEach(function(content) {
            content.classList.toggle('active', content.dataset.tab === tabName);
        });
    }

    // =========================================================================
    // Hide Selected Entity (from editor panel)
    // =========================================================================

    function hideSelectedEntity() {
        if (selectedNode) {
            hideEntity(selectedNode);
        }
    }

    // =========================================================================
    // Auto-resize Textareas
    // =========================================================================

    function autoResizeTextarea(el) {
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 200) + 'px';
    }

    // =========================================================================
    // Event Listeners
    // =========================================================================

    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            hideAddEntityModal();
            hideRuleModal();
            hidePreview();
            closeEditorPanel();
            closeContextMenu();
        }
        if (e.ctrlKey && e.key === 'b') {
            e.preventDefault();
            toggleLeftSidebar();
        }
    });

    document.addEventListener('input', function(e) {
        if (e.target.tagName === 'TEXTAREA') {
            autoResizeTextarea(e.target);
        }
    });

    document.body.addEventListener('htmx:afterSwap', function(e) {
        e.detail.elt.querySelectorAll('textarea').forEach(autoResizeTextarea);
    });

    // =========================================================================
    // Initialize
    // =========================================================================

    document.addEventListener('DOMContentLoaded', initERD);
    if (document.readyState !== 'loading') {
        setTimeout(initERD, 50);
    }

    // =========================================================================
    // Public API
    // =========================================================================

    return {
        initERD: initERD,
        refreshGraph: refreshGraph,
        deleteEntity: deleteEntity,
        updateEntity: updateEntity,
        rebuildGraph: rebuildGraph,
        autoLayout: autoLayout,
        zoomIn: zoomIn,
        zoomOut: zoomOut,
        fitGraph: fitGraph,
        showAddEntityModal: showAddEntityModal,
        hideAddEntityModal: hideAddEntityModal,
        onEntityAdded: onEntityAdded,
        submitAddEntityForm: submitAddEntityForm,
        saveSpec: saveSpec,
        startAddRelationship: startAddRelationship,
        dragNewEntity: dragNewEntity,
        dropNewEntity: dropNewEntity,
        addEntityAtPosition: addEntityAtPosition,
        showPreview: showPreview,
        hidePreview: hidePreview,
        showRuleModal: showRuleModal,
        hideRuleModal: hideRuleModal,
        showContextMenu: showContextMenu,
        closeContextMenu: closeContextMenu,
        hideEntity: hideEntity,
        showEntity: showEntity,
        showAllEntities: showAllEntities,
        toggleLeftSidebar: toggleLeftSidebar,
        switchSidebarTab: switchSidebarTab,
        hideSelectedEntity: hideSelectedEntity,
        selectEntity: selectEntity,
        closeEditorPanel: closeEditorPanel
    };
})();

// =========================================================================
// Ontology Autocomplete
// =========================================================================

window.OntologyAutocomplete = (function() {
    'use strict';

    var DEBOUNCE_MS = 300;
    var MIN_QUERY_LENGTH = 2;
    var activeRequest = null;
    var debounceTimer = null;

    function init() {
        document.querySelectorAll('[data-ontology-autocomplete]').forEach(attachAutocomplete);

        // Re-attach after HTMX swaps
        document.body.addEventListener('htmx:afterSwap', function(e) {
            e.detail.elt.querySelectorAll('[data-ontology-autocomplete]').forEach(attachAutocomplete);
        });
    }

    function attachAutocomplete(input) {
        if (input.dataset.autocompleteAttached) return;
        input.dataset.autocompleteAttached = 'true';

        var wrapper = document.createElement('div');
        wrapper.className = 'ontology-autocomplete-wrapper';
        wrapper.style.position = 'relative';
        input.parentNode.insertBefore(wrapper, input);
        wrapper.appendChild(input);

        var dropdown = document.createElement('div');
        dropdown.className = 'ontology-autocomplete-dropdown hidden';
        wrapper.appendChild(dropdown);

        input.addEventListener('input', function() {
            handleInput(input, dropdown);
        });

        input.addEventListener('keydown', function(e) {
            handleKeydown(e, input, dropdown);
        });

        input.addEventListener('blur', function() {
            setTimeout(function() { hideDropdown(dropdown); }, 200);
        });

        dropdown.addEventListener('mousedown', function(e) {
            if (e.target.classList.contains('ontology-option')) {
                selectOption(input, dropdown, e.target);
            }
        });
    }

    function handleInput(input, dropdown) {
        var query = input.value.trim();

        if (debounceTimer) clearTimeout(debounceTimer);
        if (activeRequest) activeRequest.abort();

        if (query.length < MIN_QUERY_LENGTH) {
            hideDropdown(dropdown);
            return;
        }

        debounceTimer = setTimeout(function() {
            fetchSuggestions(query, input, dropdown);
        }, DEBOUNCE_MS);
    }

    function fetchSuggestions(query, input, dropdown) {
        var ontologyFilter = input.dataset.ontologyFilter || '';
        var url = '/hub/api/ontology/suggest?q=' + encodeURIComponent(query);
        if (ontologyFilter) {
            url += '&ontology=' + encodeURIComponent(ontologyFilter);
        }

        var controller = new AbortController();
        activeRequest = controller;

        fetch(url, { signal: controller.signal })
            .then(function(response) {
                if (!response.ok) throw new Error('OLS request failed');
                return response.json();
            })
            .then(function(data) {
                renderDropdown(data, input, dropdown);
            })
            .catch(function(err) {
                if (err.name !== 'AbortError') {
                    console.error('Ontology autocomplete error:', err);
                    hideDropdown(dropdown);
                }
            })
            .finally(function() {
                activeRequest = null;
            });
    }

    function renderDropdown(data, input, dropdown) {
        var suggestions = data.suggestions || [];

        if (suggestions.length === 0) {
            dropdown.innerHTML = '<div class="ontology-no-results">No matching terms found</div>';
            showDropdown(dropdown);
            return;
        }

        var html = suggestions.map(function(s, idx) {
            var label = escapeHtml(s.label || 'Unknown');
            var id = escapeHtml(s.id || '');
            var ontology = escapeHtml(s.ontology || '');
            return '<div class="ontology-option" data-value="' + id + '" data-index="' + idx + '">' +
                   '<span class="ontology-option-label">' + label + '</span>' +
                   '<span class="ontology-option-id">' + id + '</span>' +
                   '<span class="ontology-option-source">' + ontology + '</span>' +
                   '</div>';
        }).join('');

        dropdown.innerHTML = html;
        showDropdown(dropdown);
    }

    function handleKeydown(e, input, dropdown) {
        var options = dropdown.querySelectorAll('.ontology-option');
        var current = dropdown.querySelector('.ontology-option.highlighted');
        var currentIdx = current ? parseInt(current.dataset.index) : -1;

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            var nextIdx = Math.min(currentIdx + 1, options.length - 1);
            highlightOption(options, nextIdx);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            var prevIdx = Math.max(currentIdx - 1, 0);
            highlightOption(options, prevIdx);
        } else if (e.key === 'Enter' && current) {
            e.preventDefault();
            selectOption(input, dropdown, current);
        } else if (e.key === 'Escape') {
            hideDropdown(dropdown);
        }
    }

    function highlightOption(options, idx) {
        options.forEach(function(opt) { opt.classList.remove('highlighted'); });
        if (options[idx]) {
            options[idx].classList.add('highlighted');
            options[idx].scrollIntoView({ block: 'nearest' });
        }
    }

    function selectOption(input, dropdown, option) {
        input.value = option.dataset.value;
        hideDropdown(dropdown);
        input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function showDropdown(dropdown) {
        dropdown.classList.remove('hidden');
    }

    function hideDropdown(dropdown) {
        dropdown.classList.add('hidden');
    }

    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    document.addEventListener('DOMContentLoaded', init);
    if (document.readyState !== 'loading') {
        setTimeout(init, 50);
    }

    return { init: init, attachAutocomplete: attachAutocomplete };
})();

// Expose functions globally for onclick handlers in HTML
var autoLayout = SpecBuilder.autoLayout;
var showPreview = SpecBuilder.showPreview;
var hidePreview = SpecBuilder.hidePreview;
var showAddEntityModal = SpecBuilder.showAddEntityModal;
var hideAddEntityModal = SpecBuilder.hideAddEntityModal;
var submitAddEntityForm = SpecBuilder.submitAddEntityForm;
var deleteEntity = SpecBuilder.deleteEntity;
var updateEntity = SpecBuilder.updateEntity;
var refreshGraph = SpecBuilder.refreshGraph;
var zoomIn = SpecBuilder.zoomIn;
var zoomOut = SpecBuilder.zoomOut;
var fitGraph = SpecBuilder.fitGraph;
var showRuleModal = SpecBuilder.showRuleModal;
var hideRuleModal = SpecBuilder.hideRuleModal;
var toggleLeftSidebar = SpecBuilder.toggleLeftSidebar;
var switchSidebarTab = SpecBuilder.switchSidebarTab;
var hideEntity = SpecBuilder.hideEntity;
var showEntity = SpecBuilder.showEntity;
var showAllEntities = SpecBuilder.showAllEntities;
var closeEditorPanel = SpecBuilder.closeEditorPanel;
var onEntityAdded = SpecBuilder.onEntityAdded;
var hideSelectedEntity = SpecBuilder.hideSelectedEntity;
var dragNewEntity = SpecBuilder.dragNewEntity;
var dropNewEntity = SpecBuilder.dropNewEntity;
var addEntityAtPosition = SpecBuilder.addEntityAtPosition;

/**
 * Copy YAML content to clipboard
 */
function copyYamlToClipboard(btn) {
    var container = btn.closest('.yaml-preview-container');
    var code = container.querySelector('code');
    var text = code.textContent;

    navigator.clipboard.writeText(text).then(function() {
        var originalText = btn.textContent;
        btn.textContent = 'Copied!';
        btn.classList.add('copied');
        setTimeout(function() {
            btn.textContent = originalText;
            btn.classList.remove('copied');
        }, 2000);
    }).catch(function(err) {
        console.error('Failed to copy:', err);
        btn.textContent = 'Failed';
        setTimeout(function() {
            btn.textContent = 'Copy';
        }, 2000);
    });
}
