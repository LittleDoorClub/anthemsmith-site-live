// Public API location only. Never put credentials in this file.
// Direct Railway HTTPS receiver (verified: /api/health 200, /api/orders 503 ordering_paused).
// The client joins this base with '/orders' and '/orders/<id>/checkout', so the mount is part of it.
window.ANTHEMSMITH_API_BASE = 'https://backend-production-0f3d.up.railway.app/api';
// Containment incident: the public purchase button stays shut until live verification
// passes. A paused build still restores an existing private order (recovery is preserved);
// it only refuses to create new ones. Flip to false with the release owner's written go.
window.ANTHEMSMITH_ORDERING_PAUSED = true;
