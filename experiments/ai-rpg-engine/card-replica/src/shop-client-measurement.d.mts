type TimingField = 'firstFeedbackMs' | 'firstBodyMs' | 'bodyCompleteMs' | 'allStagesCompleteMs';
type ClientMeasurement = Record<TimingField, number | null> & {
  sessionId: string;
  eventId: string;
  startedAt: string;
  source: 'browser-render-observation';
  interrupted: boolean;
  authoritative: false;
  refreshPolicy: string;
};

export function createClientMeasurements(
  storage: Pick<Storage, 'getItem' | 'setItem'>, clock?: () => number, date?: () => string,
): {
  start(sessionId: string, eventId: string): void;
  mark(sessionId: string, eventId: string, field: TimingField, visible?: boolean): void;
  export(sessionId: string): ClientMeasurement[];
};
