/**
 * Spec Builder wiring for the hub.
 *
 * All graph and editor behavior lives in metaseed's shared modules
 * (erd-common.js, spec-builder-core.js, ontology-autocomplete.js), loaded
 * from the mounted metaseed static directory before this file. This file
 * supplies only the hub-specific configuration (draft-scoped URLs, the
 * server's entity-update parameter names, the ontology suggestions endpoint)
 * and hub-only glue, then publishes the globals the templates' inline
 * handlers use. autoLayout/zoomIn/zoomOut/fitGraph come as globals from
 * erd-common.js.
 */

(function() {
    'use strict';

    // =========================================================================
    // Hub URL scheme
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
    // Hub-only UI glue
    // =========================================================================

    function updateZoomHint(scale) {
        var hint = document.getElementById('zoom-hint');
        if (!hint) return;
        if (scale < 0.5) {
            hint.classList.remove('hidden');
        } else {
            hint.classList.add('hidden');
        }
    }

    function toggleLeftSidebar() {
        var sidebar = document.getElementById('left-sidebar');
        var body = document.querySelector('.spec-builder-body');
        sidebar.classList.toggle('collapsed');
        body.classList.toggle('sidebar-collapsed');
        setTimeout(function() {
            var network = graph.getNetwork();
            if (network) network.fit({ animation: { duration: 200 } });
        }, 250);
    }

    function startAddRelationship() {
        alert('To add a relationship:\n1. Click on the source entity\n2. Add a field of type "entity" or "list"\n3. Set the Items/Target to the target entity name');
    }

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

    function autoResizeTextarea(el) {
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 200) + 'px';
    }

    // =========================================================================
    // Shared module configuration
    // =========================================================================

    var graph = SpecBuilderGraph.create({
        getEntities: function() { return window.entities || {}; },
        rootEntity: function() { return window.rootEntity; },
        url: apiUrl,
        // The hub server expects new_name/description/ontology_term.
        updateEntityBody: function(formData, oldName) {
            return 'new_name=' + encodeURIComponent(formData.get('name') || '') +
                   '&description=' + encodeURIComponent(formData.get('description') || '') +
                   '&ontology_term=' + encodeURIComponent(formData.get('ontology_term') || '');
        },
        onNetworkReady: function(network) {
            network.on('zoom', function(params) {
                updateZoomHint(params.scale);
            });
        }
    });

    OntologyAutocomplete.create({
        suggestUrl: function(query, input) {
            // Support both data-ontologies (comma-separated list) and
            // data-ontology-filter (single ontology).
            var ontologies = input.dataset.ontologies || input.dataset.ontologyFilter || '';
            var url = '/hub/api/ontology/suggest?q=' + encodeURIComponent(query);
            if (ontologies) {
                url += '&ontology=' + encodeURIComponent(ontologies);
            }
            return url;
        }
    });

    // =========================================================================
    // Hub-only event listeners
    // =========================================================================

    document.addEventListener('keydown', function(e) {
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
    // Globals for the templates' inline handlers
    // =========================================================================

    window.toggleLeftSidebar = toggleLeftSidebar;
    window.startAddRelationship = startAddRelationship;
    window.copyYamlToClipboard = copyYamlToClipboard;

    window.showAddEntityModal = graph.showAddEntityModal;
    window.hideAddEntityModal = graph.hideAddEntityModal;
    window.onEntityAdded = graph.onEntityAdded;
    window.showPreview = graph.showPreview;
    window.hidePreview = graph.hidePreview;
    window.showRuleModal = graph.showRuleModal;
    window.hideRuleModal = graph.hideRuleModal;
    window.switchSidebarTab = graph.switchSidebarTab;
    window.hideEntity = graph.hideEntity;
    window.showEntity = graph.showEntity;
    window.showAllEntities = graph.showAllEntities;
    window.hideSelectedEntity = graph.hideSelectedEntity;
    window.closeEditorPanel = graph.closeEditorPanel;
    window.dropNewEntity = graph.dropNewEntity;
    window.addEntityAtPosition = graph.addEntityAtPosition;
})();
