import React from 'react';
import { getCardSymbol, getCardColor } from '../../utils/cardUtils';

interface CardProps {
  card: string;
  size?: 'small' | 'medium' | 'large' | 'xlarge';
  className?: string;
}

const Card: React.FC<CardProps> = ({ card, size = 'medium', className = '' }) => {
  const symbol = getCardSymbol(card);
  const color = getCardColor(card);

  return (
    <div className={`playing-card ${size} ${color} ${className}`}>
      <div className="card-content">
        <span className="card-symbol">{symbol}</span>
      </div>
    </div>
  );
};

export default Card;
