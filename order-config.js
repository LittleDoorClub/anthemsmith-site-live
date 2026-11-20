// Public API location only. Never put credentials in this file.
// Deployment must provide /api or set this to the approved HTTPS API origin.
window.ANTHEMSMITH_API_BASE = '/api';
// Containment AS-MUIJ11B6: the public purchase button stays shut until live verification
// passes. A paused build still restores an existing private order (recovery is preserved);
// it only refuses to create new ones. Flip to false with the release owner's written go.
window.ANTHEMSMITH_ORDERING_PAUSED = true;
