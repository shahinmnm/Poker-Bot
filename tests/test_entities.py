from pokerapp.entities import Game


def test_reset_defaults_dealer_to_zero():
    game = Game()
    game.dealer_index = 2
    game.players = [object()]

    game.reset()

    assert game.dealer_index == 0


def test_reset_rotate_dealer_cycles_through_players():
    game = Game()
    # Simulate an existing table with three players.
    game.players = [object(), object(), object()]

    game.reset(rotate_dealer=True)
    assert game.dealer_index == 1

    game.players = [object(), object(), object()]
    game.reset(rotate_dealer=True)
    assert game.dealer_index == 2

    game.players = [object(), object(), object()]
    game.reset(rotate_dealer=True)
    assert game.dealer_index == 0
