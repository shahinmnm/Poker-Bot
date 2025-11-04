export const getCardSymbol = (card: string): string => {
  const suits: Record<string, string> = {
    h: '♥️',
    d: '♦️',
    c: '♣️',
    s: '♠️',
  };

  if (card.length < 2) return card;

  const rank = card.slice(0, -1);
  const suit = card.slice(-1).toLowerCase();

  return `${rank}${suits[suit] || suit}`;
};

export const getCardColor = (card: string): 'red' | 'black' => {
  const suit = card.slice(-1).toLowerCase();
  return suit === 'h' || suit === 'd' ? 'red' : 'black';
};

export const formatChips = (amount: number): string => {
  if (amount >= 1_000_000) {
    return `${(amount / 1_000_000).toFixed(1)}M`;
  }
  if (amount >= 1_000) {
    return `${(amount / 1_000).toFixed(1)}K`;
  }
  return amount.toString();
};
