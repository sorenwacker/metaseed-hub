/**
 * Shared Graph Configuration
 *
 * Common vis.js network configuration for all graph visualizations.
 */

window.GraphConfig = (function() {
    'use strict';

    const PHYSICS = {
        solver: 'forceAtlas2Based',
        forceAtlas2Based: {
            gravitationalConstant: -100,
            centralGravity: 0.01,
            springLength: 200,
            springConstant: 0.08,
            damping: 0.5,
            avoidOverlap: 1
        },
        maxVelocity: 50,
        minVelocity: 0.1,
        stabilization: {
            enabled: true,
            iterations: 2000,
            updateInterval: 25
        }
    };

    /**
     * Get physics configuration with optional overrides.
     * @param {Object} overrides - Properties to override in the base config
     * @returns {Object} Physics configuration for vis.js
     */
    function getPhysicsConfig(overrides) {
        overrides = overrides || {};
        const config = {
            enabled: true,
            solver: PHYSICS.solver,
            forceAtlas2Based: Object.assign({}, PHYSICS.forceAtlas2Based),
            maxVelocity: PHYSICS.maxVelocity,
            minVelocity: PHYSICS.minVelocity,
            stabilization: Object.assign({}, PHYSICS.stabilization)
        };

        if (overrides.gravitationalConstant !== undefined) {
            config.forceAtlas2Based.gravitationalConstant = overrides.gravitationalConstant;
        }
        if (overrides.springLength !== undefined) {
            config.forceAtlas2Based.springLength = overrides.springLength;
        }
        if (overrides.springConstant !== undefined) {
            config.forceAtlas2Based.springConstant = overrides.springConstant;
        }
        if (overrides.stabilization !== undefined) {
            config.stabilization = Object.assign({}, config.stabilization, overrides.stabilization);
        }
        if (overrides.enabled !== undefined) {
            config.enabled = overrides.enabled;
        }

        return config;
    }

    return {
        PHYSICS: PHYSICS,
        getPhysicsConfig: getPhysicsConfig
    };
})();
