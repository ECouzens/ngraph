import gym
from ngraph.frontends.neon.dqn import Agent


def rl_loop(environment, agent, episodes, render=False):
    """
    train an agent inside an environment for a set number of episodes

    # todo: rename to rl_loop_train, and follow the same pattern and callbacks
    # neon.loop_train used for supervised learning.
    """
    total_steps = 0
    for episode in range(episodes):
        state = environment.reset()
        done = False
        step = 0
        total_reward = 0
        trigger_evaluation = False
        while not done:
            if render:
                environment.render()

            action = agent.act(state)
            next_state, reward, done, _ = environment.step(action)
            agent.observe_results(
                state, action, reward, next_state, done, total_steps
            )

            state = next_state
            step += 1
            total_steps += 1
            total_reward += reward

            if total_steps % 50000 == 0:
                trigger_evaluation = True

        agent.end_of_episode()
        print(
            'episode: {}, total_steps: {}, steps: {}, last_reward: {}, '
            'sum(reward): {}'.format(
                episode, total_steps, step, reward, total_reward
            )
        )

        # we would like to evaluate the model at a consistent time measured
        # in update steps, but we can't start an evaluation in the middle of an
        # episode.  if we have accumulated enough updates to warrant an evaluation
        # set trigger_evaluation to true, and run an evaluation at the end of
        # the episode.
        if trigger_evaluation:
            trigger_evaluation = False
            print(
                'evaluation episode. total_steps: {}, total reward: {}'.format(
                    total_steps,
                    evaluate_single_episode(environment, agent, render)
                )
            )


def evaluate_single_episode(environment, agent, render=False):
    """
    evaluate a single episode of agent operating inside of an environment
    """
    state = environment.reset()
    done = False
    step = 0
    total_reward = 0
    while not done:
        if render:
            environment.render()

        action = agent.act(state, training=False)
        next_state, reward, done, _ = environment.step(action)

        state = next_state
        step += 1
        total_reward += reward

    agent.end_of_episode()

    return total_reward
