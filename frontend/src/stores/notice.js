let pending = '';
export function notice(message) { pending = message; }
export function takeNotice() { const value = pending; pending = ''; return value; }
