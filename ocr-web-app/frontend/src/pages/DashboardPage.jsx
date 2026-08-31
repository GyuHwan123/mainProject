import { useEffect, useMemo, useState } from 'react';

import Sidebar from '../components/Sidebar';
import LoginLoading from '../components/LoginLoading';
import AiAgentChat from '../components/dashboard/AiAgentChat';
import DashboardCalendar from '../components/dashboard/DashboardCalendar';
import RecentMeetings from '../components/dashboard/RecentMeetings';
import TaskList from '../components/dashboard/TaskList';
import {createDashboardEvent, createDashboardMeeting, createDashboardTask, deleteDashboardEvent, deleteDashboardMeeting, deleteDashboardTask, getDashboardData, sendAgentMessage, updateDashboardEvent, updateDashboardMeeting, updateDashboardTask,} from '../features/dashboardService';
import '../style/DashboardPage.scss';
import '../style/DashboardInteractions.scss';
import '../style/DashboardAgentActions.scss';
import '../style/DashboardAgentLayout.scss';
import '../style/DashboardAgentProposals.scss';

const dateKey = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;

export default function DashboardPage() {
  const [selectedDate, setSelectedDate] = useState(() => dateKey(new Date()));
  const [selectedMeeting, setSelectedMeeting] = useState(null);
  const [taskFilter, setTaskFilter] = useState('ALL');
  const [events, setEvents] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [meetings, setMeetings] = useState([]);
  const [apiNotice, setApiNotice] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;

    getDashboardData()
      .then((data) => {
        if (active) {
          setEvents(data.events);
          setTasks(data.tasks);
          setMeetings(data.meetings);
          setApiNotice('');
        }
      })
      .catch(() => {
        if (active) setApiNotice('대시보드 데이터를 불러오지 못했습니다. FastAPI와 Supabase 연결을 확인해 주세요.');
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => { active = false; };
  }, []);

  const selectedEvents = useMemo(
    () => events.filter((event) => event.date === selectedDate),
    [events, selectedDate],
  );

  const focusMeeting = (meeting) => {
    setSelectedMeeting(meeting);
    setTaskFilter('ALL');
    document.querySelector('#dashboard-tasks')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  const addEvent = async (values) => {
    const item = await createDashboardEvent(values);
    setEvents((list) => [...list, item]);
    setSelectedDate(item.date);
    return item;
  };

  const editEvent = async (id, values) => {
    const item = await updateDashboardEvent(id, values);
    setEvents((list) => list.map((old) => (old.id === id ? item : old)));
    setSelectedDate(item.date);
  };

  const removeEvent = async (id) => {
    await deleteDashboardEvent(id);
    setEvents((list) => list.filter((item) => item.id !== id));
  };

  const addTask = async (values) => {
    const item = await createDashboardTask(values);
    setTasks((list) => [item, ...list]);
    setSelectedMeeting(null);
    return item;
  };

  const editTask = async (id, values) => {
    const item = await updateDashboardTask(id, values);
    setTasks((list) => list.map((old) => (old.id === id ? item : old)));
  };

  const removeTask = async (id) => {
    await deleteDashboardTask(id);
    setTasks((list) => list.filter((item) => item.id !== id));
  };

  const addMeeting = async (values) => {
    const item = await createDashboardMeeting(values);
    setMeetings((list) => [item, ...list]);
    return item;
  };

  const editMeeting = async (id, values) => {
    const item = await updateDashboardMeeting(id, values);
    setMeetings((list) => list.map((old) => (old.id === id ? item : old)));
    setSelectedMeeting((old) => (old?.id === id ? item : old));
  };

  const removeMeeting = async (id) => {
    await deleteDashboardMeeting(id);
    setMeetings((list) => list.filter((item) => item.id !== id));
    setSelectedMeeting((old) => (old?.id === id ? null : old));
  };

  const todayLabel = new Intl.DateTimeFormat('ko-KR', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());

  if (loading) {
    return (
      <div className="app-shell dashboard-shell">
        <Sidebar />
        <main className="page-loading-region">
          <LoginLoading
            mode="content"
            title="대시보드를 불러오는 중입니다."
            ariaLabel="대시보드 불러오는 중"
          />
        </main>
      </div>
    );
  }

  return (
    <div className="app-shell dashboard-shell">
      <Sidebar />
      <main className="work-dashboard page-enter">
        <header className="dashboard-heading">
          <div>
            <p className="eyebrow">WORKSPACE OVERVIEW</p>
            <h1>대시보드</h1>
            <p>오늘의 일정과 회의에서 이어진 업무를 한눈에 확인하세요.</p>
          </div>
          <div className="dashboard-date">
            <span>오늘</span>
            <strong>{todayLabel}</strong>
          </div>
        </header>

        {apiNotice && (
          <button className="api-notice" onClick={() => setApiNotice('')}>
            {apiNotice}<span>×</span>
          </button>
        )}

        <section className="dashboard-grid dashboard-grid-middle">
          <DashboardCalendar
            events={events}
            selectedDate={selectedDate}
            onSelectDate={setSelectedDate}
            selectedEvents={selectedEvents}
            onAdd={addEvent}
            onUpdate={editEvent}
            onDelete={removeEvent}
          />
          <RecentMeetings
            meetings={meetings}
            selectedMeeting={selectedMeeting}
            onSelectMeeting={focusMeeting}
            onAdd={addMeeting}
            onUpdate={editMeeting}
            onDelete={removeMeeting}
            onAddTask={addTask}
            onAddEvent={addEvent}
          />
        </section>

        <section className="dashboard-grid dashboard-grid-bottom">
          <TaskList
            tasks={tasks}
            filter={taskFilter}
            onFilter={setTaskFilter}
            selectedMeeting={selectedMeeting}
            onAdd={addTask}
            onUpdate={editTask}
            onDelete={removeTask}
          />
          <AiAgentChat
            onSend={sendAgentMessage}
            onAddEvent={addEvent}
            onAddTask={addTask}
            events={events}
            tasks={tasks}
            meetings={meetings}
          />
        </section>
      </main>
    </div>
  );
}
