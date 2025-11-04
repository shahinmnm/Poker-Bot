import React from 'react';
import Card from '../UI/Card';

interface CommunityCardsProps {
  cards: string[];
}

const CommunityCards: React.FC<CommunityCardsProps> = ({ cards }) => {
  const cardSlots = Array(5)
    .fill(null)
    .map((_, i) => cards[i] || null);

  return (
    <div className="community-cards">
      <div className="cards-container">
        {cardSlots.map((card, index) => (
          <div key={index} className="card-slot">
            {card ? (
              <Card card={card} size="large" />
            ) : (
              <div className="card-placeholder">
                <span className="card-back">🂠</span>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};

export default CommunityCards;
