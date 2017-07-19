import gym
from collections import deque
import numpy as np
import cv2
import simple_environments
from ngraph.frontends.neon import dqn, rl_loop
from ngraph.frontends import neon
import ngraph as ng

# factory = ng.transformers.make_transformer_factory('gpu')
# mg.transformers.set_transformer_factory(factory)


def model(action_axes):
    """
    Given the expected action axes, return a model mapping from observation to
    action axes for use by the dqn agent.
    """
    return neon.Sequential([
        neon.Convolution(
            (8, 8, 32),
            neon.XavierInit(),
            strides=4,
            activation=neon.Rectlin(),
            batch_norm=True,
        ),
        neon.Convolution(
            (4, 4, 64),
            neon.XavierInit(),
            strides=2,
            activation=neon.Rectlin(),
            batch_norm=True,
        ),
        neon.Convolution(
            (3, 3, 64),
            neon.XavierInit(),
            strides=1,
            activation=neon.Rectlin(),
            batch_norm=True,
        ),
        neon.Affine(
            nout=512,
            weight_init=neon.XavierInit(),
            bias_init=neon.ConstantInit(),
            activation=neon.Rectlin(),
            batch_norm=True,
        ),
        neon.Affine(
            weight_init=neon.XavierInit(),
            bias_init=neon.ConstantInit(),
            activation=neon.Rectlin(),
            batch_norm=True,
            axes=(action_axes, )
        ),
    ])


class ReshapeWrapper(gym.Wrapper):
    """
    Reshape the observation provided by open ai gym atari environment to match
    the deepmind dqn paper.
    """

    def __init__(self, environment):
        super(ReshapeWrapper, self).__init__(environment)

        self.observation_space = gym.spaces.Box(low=0, high=1, shape=(84, 84))

    def _modify_observation(self, observation):
        # convert color to grayscale
        observation = np.mean(observation, axis=2)
        # resize image to 84, 84
        observation = cv2.resize(observation, (84, 84))
        # convert to values between 0 and 1
        observation = observation / 256.

        return observation

    def _step(self, action):
        observation, reward, done, info = self.env._step(action)

        observation = self._modify_observation(observation)

        return observation, reward, done, info

    def _reset(self):
        return self._modify_observation(self.env._reset())


class ClipRewardWrapper(gym.Wrapper):
    """
    wraps an environment so that the reward is always either -1, 0 or 1
    """

    def _step(self, action):
        observation, reward, done, info = self.env._step(action)

        # clip reward to -1, 0, or 1
        if reward > 0:
            reward = 1
        elif reward < 0:
            reward = -1
        else:
            reward = 0

        return observation, reward, done, info


def RepeatWrapper(frames=4):
    """
    Generate an wrapper class. I am not sure why open ai gym thinks this
    is a good pattern to use instead of passing frames as a argument to
    the constructor.
    """

    class RepeatWrapper(gym.Wrapper):
        """
        Send multiple steps of observations to agent at each step
        """

        def __init__(self, env):
            super(RepeatWrapper, self).__init__(env)
            self.frames = frames
            self.history = []

            # todo: this shouldn't always be a box, low and high aren't
            #       always 0 and 1
            self.observation_space = gym.spaces.Box(
                low=0,
                high=1,
                shape=(frames, ) + self.observation_space.shape,
            )

        def _reset(self):
            self.history = deque([super(RepeatWrapper, self)._reset()],
                                 maxlen=self.frames)

            # take random actions to start and fill frame buffer
            for _ in range(self.frames - 1):
                action = self.env.action_space.sample()
                observation, reward, done, info = self.env._step(action)
                self.history.append(observation)
                assert done != True

            return np.stack(self.history, axis=0)

        def _step(self, action):
            observation, reward, done, info = self.env._step(action)

            self.history.append(observation)

            return np.stack(self.history, axis=0), reward, done, info

    return RepeatWrapper


def main():
    # deterministic version 4 results in a frame skip of 4 and no repeat action probability
    # todo: total_reward isn't always greater than 95 even with a working implementation
    # environment = gym.make('SpaceInvaders-v0')
    environment = gym.make('BreakoutDeterministic-v4')
    environment = ReshapeWrapper(environment)
    environment = ClipRewardWrapper(environment)
    environment = RepeatWrapper(frames=4)(environment)

    # todo: perhaps these should be defined in the environment itself
    state_axes = ng.make_axes([
        ng.make_axis(environment.observation_space.shape[0], name='feature'),
        ng.make_axis(environment.observation_space.shape[1], name='width'),
        ng.make_axis(environment.observation_space.shape[2], name='height'),
    ])

    agent = dqn.Agent(
        state_axes,
        environment.action_space,
        model=model,
        epsilon=dqn.linear_generator(start=1.0, end=0.1, steps=1000000),
        gamma=0.99,
        learning_rate=0.00025,
        memory=dqn.RepeatMemory(
            frames_per_observation=4,
            maxlen=1000000,
            observation_shape=(84, 84),
        ),
    )

    rl_loop.rl_loop(environment, agent, episodes=20000)

    total_reward = rl_loop.evaluate_single_episode(environment, agent)


if __name__ == "__main__":
    main()
