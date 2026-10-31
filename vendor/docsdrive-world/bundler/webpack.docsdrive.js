// DocsDrive integration build of the vendored 3D workstation environment.
// Based on bundler/webpack.prod.js from henryjeff/portfolio-website (MIT).
// Outputs to the Next.js public directory so the world is served at
// /docsdrive-world/ from the same origin as the DocsDrive site.
const { merge } = require('webpack-merge');
const commonConfiguration = require('./webpack.common.js');
const path = require('path');

module.exports = merge(commonConfiguration, {
    mode: 'production',
    output: {
        path: path.resolve(__dirname, '../../../public/docsdrive-world'),
        publicPath: '/docsdrive-world/',
    },
});
