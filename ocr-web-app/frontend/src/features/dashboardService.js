import apiClient from '../api/client';
export const getDashboardData = async () => {
  const [schedules,tasks,meetings,briefing]=await Promise.all([apiClient.get('/dashboard/schedules'),apiClient.get('/dashboard/tasks'),apiClient.get('/dashboard/meetings'),apiClient.get('/dashboard/briefing')]);
  return { events:schedules.data,tasks:tasks.data,meetings:meetings.data,briefing:briefing.data };
};
export const createDashboardEvent = async (event) => (await apiClient.post('/dashboard/schedules',event)).data;
export const updateDashboardEvent = async (id,event) => (await apiClient.put(`/dashboard/schedules/${id}`,event)).data;
export const deleteDashboardEvent = async (id) => apiClient.delete(`/dashboard/schedules/${id}`);
export const createDashboardTask = async (task) => (await apiClient.post('/dashboard/tasks',task)).data;
export const updateDashboardTask = async (id,task) => (await apiClient.put(`/dashboard/tasks/${id}`,task)).data;
export const deleteDashboardTask = async (id) => apiClient.delete(`/dashboard/tasks/${id}`);
export const createDashboardMeeting = async (meeting) => (await apiClient.post('/dashboard/meetings',meeting)).data;
export const updateDashboardMeeting = async (id,meeting) => (await apiClient.put(`/dashboard/meetings/${id}`,meeting)).data;
export const deleteDashboardMeeting = async (id) => apiClient.delete(`/dashboard/meetings/${id}`);
const participantSuggestionCache=new Map();
export const getParticipantSuggestions = async (query='') => {
  const key=query.trim().toLowerCase();
  if(participantSuggestionCache.has(key))return participantSuggestionCache.get(key);
  const data=(await apiClient.get('/dashboard/participant-suggestions',{params:{q:key}})).data;
  participantSuggestionCache.set(key,data);return data;
};
export const getMeetingShares = async (id) => (await apiClient.get(`/dashboard/meetings/${id}/shares`)).data;
export const inviteMeetingShare = async (id,payload) => (await apiClient.post(`/dashboard/meetings/${id}/shares`,payload)).data;
export const deleteMeetingShare = async (meetingId,shareId) => apiClient.delete(`/dashboard/meetings/${meetingId}/shares/${shareId}`);
export const getMeetingInvitations = async () => (await apiClient.get('/dashboard/meeting-invitations')).data;
export const respondMeetingInvitation = async (meetingId,status) => (await apiClient.post(`/dashboard/meetings/${meetingId}/shares/respond`,{status})).data;
export const getDashboardBriefing = async () => (await apiClient.get('/dashboard/briefing')).data;
export const sendAgentMessage = async (message,history=[]) => (await apiClient.post('/agent/chat',{message,history},{timeout:120000})).data;
export const extractMeetingActions = async (meetingId) => (await apiClient.post('/agent/extract-actions',{meeting_id:meetingId},{timeout:120000})).data;
