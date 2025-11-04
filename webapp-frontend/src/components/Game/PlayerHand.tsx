import React from 'react';
import Card from '../UI/Card';

interface PlayerHandProps {
  cards: string[];
}

const PlayerHand: React.FC<PlayerHandProps> = ({ cards }) => {
  return (
    <div className="player-hand">
      <div className="hand-label">Your Cards</div>
      <div className="hand-cards">
        {cards.length === 0 ? (
          <>
            <div className="card-back">🂠</div>
            <div className="card-back">🂠</div>
          </>
        ) : (
          cards.map((card, index) => <Card key={index} card={card} size="xlarge" />)
        )}
      </div>
    </div>
  );
};

export default PlayerHand;
