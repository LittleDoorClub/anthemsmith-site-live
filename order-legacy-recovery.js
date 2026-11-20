// Older links carry an order reference, which is sufficient to ask support for help.
window.restoreLegacyOrder = function (search, hash, brief, hasSavedPrivateOrder) {
  const privateFields = ['order', 'order_id', 'token', 'access', 'session_id'];
  if (privateFields.some(key => search.has(key) || hash.has(key))) return false;

  const validId = value => typeof value === 'string' && /^AS-[A-Z0-9-]{1,64}$/.test(value);
  let orderId;
  if (search.has('oid')) {
    // The link the customer opened takes priority over old tabs and stored orders.
    const ids = search.getAll('oid');
    if (ids.length === 1 && validId(ids[0])) orderId = ids[0];
  } else {
    if (hasSavedPrivateOrder()) return false;
    try {
      const stored = JSON.parse(sessionStorage.getItem('anthemsmith_order'));
      if (validId(stored?.request_id)) orderId = stored.request_id;
    } catch { /* A customer may block storage or have an unreadable old record. */ }
    if (!orderId && search.get('paid') !== '1') return false;
  }

  const heading = document.createElement('h2');
  heading.textContent = 'Help with your existing order';
  const message = document.createElement('p');
  message.textContent = 'Open the private order or song link from your original confirmation or delivery message. If you cannot find it, email us for help. You do not need to pay again.';
  brief.replaceChildren(heading, message);
  if (orderId) {
    const reference = document.createElement('p');
    reference.textContent = 'Order ' + orderId;
    brief.append(reference);
  }
  const email = document.createElement('a');
  const subject = orderId ? 'Help with AnthemSmith order ' + orderId : 'Help with my AnthemSmith order';
  email.href = 'mailto:cozyafllc@gmail.com?subject=' + encodeURIComponent(subject);
  email.textContent = 'Email AnthemSmith support';
  brief.append(email);
  brief.hidden = false;
  if (search.has('oid') || search.get('paid') === '1') brief.scrollIntoView({block: 'start'});
  return true;
};
