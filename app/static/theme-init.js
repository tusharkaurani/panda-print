// Runs before first paint so the saved theme applies without a flash.
try {
  var t = localStorage.getItem('pp-theme');
  if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t);
} catch (e) { /* storage blocked: follow the OS */ }
