#!/usr/bin/env python3
"""
Betting round management and pot calculation.
Handles side pots for complex all-in scenarios.
"""

import logging
from typing import Dict, List, Tuple
from pokerapp.entities import Game, Money, Player, Score
from pokerapp.cards import Cards

logger = logging.getLogger(__name__)


class SidePot:
    """Represents one side pot in complex all-in scenarios"""

    def __init__(self, amount: int, eligible_players: List[Player]):
        self.amount = amount
        self.eligible_players = eligible_players

    def __repr__(self):
        player_ids = [p.user_id for p in self.eligible_players]
        return f"SidePot(amount={self.amount}, players={player_ids})"


class SidePotCalculator:
    """
    Calculate side pots for unequal all-in amounts.
    """

    def calculate_side_pots(self, game: Game) -> List[SidePot]:
        """
        Create side pots based on player contributions.

        Returns:
            List of SidePot objects ordered from main → side → side...
        """
        # Get all players with money in pot (authorized amounts)
        player_contributions = []
        for player in game.players:
            contributed = player.wallet.authorized_money(game.id)
            if contributed > 0:
                player_contributions.append((player, contributed))

        # Sort by contribution amount (lowest first)
        player_contributions.sort(key=lambda x: x[1])

        side_pots = []
        remaining_players = [p[0] for p in player_contributions]
        previous_level = 0

        for player, contribution in player_contributions:
            pot_level = contribution - previous_level

            if pot_level > 0:
                # Create pot for this level
                pot_amount = pot_level * len(remaining_players)
                side_pot = SidePot(
                    amount=pot_amount,
                    eligible_players=remaining_players.copy()
                )
                side_pots.append(side_pot)

            # Remove this player from future pots.
            # They're all-in at the current contribution level.
            remaining_players.remove(player)
            previous_level = contribution

        return side_pots

    def distribute_pots(
        self,
        side_pots: List[SidePot],
        player_scores: Dict[Score, List[Tuple[Player, Cards]]]
    ) -> List[Tuple[Player, Cards, Money]]:
        """
        Distribute each side pot to winners.

        Args:
            side_pots: Side pots from main to highest
            player_scores: Winner rankings by hand strength

        Returns:
            List of (player, winning_hand, amount_won)
        """
        results = []

        # Sort winners by score (best hand first)
        sorted_scores = sorted(
            player_scores.items(),
            key=lambda x: x[0],
            reverse=True
        )

        for side_pot in side_pots:
            if side_pot.amount <= 0:
                continue

            # Find eligible winners for this pot
            eligible_player_ids = {
                p.user_id for p in side_pot.eligible_players
            }

            pot_winners = []
            for score, players_hands in sorted_scores:
                for player, hand in players_hands:
                    if player.user_id in eligible_player_ids:
                        pot_winners.append((player, hand))

                if pot_winners:
                    # Distribute pot equally among winners of same score level
                    share = side_pot.amount / len(pot_winners)

                    for player, hand in pot_winners:
                        win_amount = int(round(share))
                        player.wallet.inc(win_amount)
                        results.append((player, hand, win_amount))

                    break  # move to next side pot once distributed

        return results
