const greeting = { role: 'assistant', text: '안녕하세요. 문서에서 궁금한 내용을 질문해 주세요.' };

// A user message is restorable only with its completed assistant response.
// This also discards orphan questions saved by older clients.
export function completedChatMessages(messages) {
  const completed = [];
  let question = null;
  for (const message of messages || []) {
    if (message.role === 'user') {
      question = message.status === 'pending' ? null : message;
    } else if (message.role === 'assistant') {
      if (question) {
        completed.push(question, message);
        question = null;
      } else if (!completed.length && !(messages || []).some((item) => item.role === 'user')) {
        completed.push(message);
      }
    }
  }
  return completed.length ? completed : [greeting];
}
