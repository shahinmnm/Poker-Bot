export const formatTimeAgo = (dateString: string): string => {
  const date = new Date(dateString);
  const now = new Date();
  if (Number.isNaN(date.getTime())) return 'just now';
  const seconds = Math.floor((now.getTime() - date.getTime()) / 1000);

  if (seconds < 60) return 'just now';
  if (seconds < 3_600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3_600)}h ago`;
  return `${Math.floor(seconds / 86_400)}d ago`;
};

export const getGameStateText = (state: string): string => {
  const stateMap: Record<string, string> = {
    INITIAL: 'Waiting for players',
    ROUND_PRE_FLOP: 'Pre-flop',
    ROUND_FLOP: 'Flop',
    ROUND_TURN: 'Turn',
    ROUND_RIVER: 'River',
    PRE_FLOP: 'Pre-flop',
    FLOP: 'Flop',
    TURN: 'Turn',
    RIVER: 'River',
    FINISHED: 'Finished',
  };
  return stateMap[state] || state;
};
